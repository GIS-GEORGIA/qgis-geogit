"""Pixel-level comparison of two versions of a raster with GDAL and numpy.

No QGIS imports. Works block by block, so a raster of a gigabyte or more never
has to fit in memory. When the grids differ (other size, extent or CRS), the
new version is warped onto the old grid before comparing.
"""

import os
from dataclasses import dataclass, field

import numpy as np
from osgeo import gdal, ogr, osr

BLOCK = 1024
MAX_MASK_PIXELS = 4_000_000   # changed-area outlines are traced on a grid this small at most


@dataclass
class RasterInfo:
    width: int
    height: int
    bands: int
    dtype: str
    geotransform: tuple
    srs_wkt: str
    nodata: list

    def describe(self):
        px = abs(self.geotransform[1]) if self.geotransform else 0
        return f"{self.width}×{self.height}, {self.bands}b {self.dtype}, {px:g}"


@dataclass
class RasterDiff:
    old: RasterInfo = None
    new: RasterInfo = None
    meta_changes: list = field(default_factory=list)    # keys: size, bands, dtype, grid, crs, nodata
    compared: bool = False
    changed_pixels: int = 0
    valid_pixels: int = 0
    gained_pixels: int = 0       # nodata in old, data in new
    lost_pixels: int = 0         # data in old, nodata in new
    min_diff: float = 0.0
    max_diff: float = 0.0
    mean_abs_diff: float = 0.0
    bbox: tuple = None           # changed area (minx, miny, maxx, maxy) in the old CRS
    diff_path: str = ""          # Float32 GeoTIFF: new - old (1 band) or largest band change; 0 = unchanged
    mask_path: str = ""          # GeoPackage with polygons around changed pixels

    @property
    def changed(self):
        return bool(self.meta_changes or self.changed_pixels)

    @property
    def changed_percent(self):
        return 100.0 * self.changed_pixels / self.valid_pixels if self.valid_pixels else 0.0


def info(path):
    ds = gdal.Open(path)
    if ds is None:
        raise ValueError(f"cannot open {path}: {gdal.GetLastErrorMsg()}")
    b1 = ds.GetRasterBand(1) if ds.RasterCount else None
    return RasterInfo(ds.RasterXSize, ds.RasterYSize, ds.RasterCount,
                      gdal.GetDataTypeName(b1.DataType) if b1 else "",
                      tuple(round(v, 9) for v in ds.GetGeoTransform(can_return_null=True) or ()),
                      ds.GetProjection() or "",
                      [ds.GetRasterBand(i + 1).GetNoDataValue() for i in range(ds.RasterCount)])


def _same_crs(a, b):
    if not a or not b:
        return a == b
    sa, sb = osr.SpatialReference(), osr.SpatialReference()
    sa.ImportFromWkt(a)
    sb.ImportFromWkt(b)
    return bool(sa.IsSame(sb))


def _meta_changes(old, new):
    out = []
    if (old.width, old.height) != (new.width, new.height):
        out.append("size")
    if old.bands != new.bands:
        out.append("bands")
    if old.dtype != new.dtype:
        out.append("dtype")
    if old.geotransform != new.geotransform:
        out.append("grid")
    if not _same_crs(old.srs_wkt, new.srs_wkt):
        out.append("crs")
    if old.nodata != new.nodata:
        out.append("nodata")
    return out


def _aligned(old_path, new_path, old, out_dir):
    """new_path as a dataset on old's grid (a VRT when it has to be warped)."""
    new = info(new_path)
    if (new.width, new.height, new.geotransform) == (old.width, old.height, old.geotransform) \
            and _same_crs(old.srs_wkt, new.srs_wkt):
        return gdal.Open(new_path)
    gt = old.geotransform
    bounds = (gt[0], gt[3] + gt[5] * old.height, gt[0] + gt[1] * old.width, gt[3])
    vrt = os.path.join(out_dir, "aligned.vrt")
    return gdal.Warp(vrt, new_path, format="VRT", outputBounds=bounds, width=old.width,
                     height=old.height, dstSRS=old.srs_wkt or None, resampleAlg="near")


def _valid(arr, nodata):
    ok = np.ones(arr.shape, dtype=bool)
    if nodata is not None:
        ok &= arr != nodata
    if arr.dtype.kind == "f":
        ok &= ~np.isnan(arr)
    return ok


def compare(old_path, new_path, out_dir, tolerance=0.0):
    """RasterDiff between two raster files; output rasters go to out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    result = RasterDiff(info(old_path), info(new_path))
    result.meta_changes = _meta_changes(result.old, result.new)
    old = result.old
    if old.bands != result.new.bands or not old.geotransform:
        return result      # different band layout or no georeferencing: metadata only

    ds_old = gdal.Open(old_path)
    ds_new = _aligned(old_path, new_path, old, out_dir)
    nd_old = old.nodata
    nd_new = [ds_new.GetRasterBand(i + 1).GetNoDataValue() for i in range(old.bands)]

    diff_path = os.path.join(out_dir, "diff.tif")
    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(diff_path, old.width, old.height, 1, gdal.GDT_Float32,
                     ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"])
    out.SetGeoTransform(old.geotransform)
    if old.srs_wkt:
        out.SetProjection(old.srs_wkt)
    out_band = out.GetRasterBand(1)
    out_band.SetNoDataValue(0)
    mask_full = gdal.GetDriverByName("MEM" if old.width * old.height <= MAX_MASK_PIXELS else "GTiff")
    mask_file = "" if mask_full.ShortName == "MEM" else os.path.join(out_dir, "mask.tif")
    mask = mask_full.Create(mask_file, old.width, old.height, 1, gdal.GDT_Byte,
                            [] if not mask_file else ["COMPRESS=DEFLATE", "TILED=YES"])
    mask.SetGeoTransform(old.geotransform)
    if old.srs_wkt:
        mask.SetProjection(old.srs_wkt)

    changed = valid = gained = lost = 0
    dmin, dmax, abs_sum = np.inf, -np.inf, 0.0
    rows = cols = None
    for y in range(0, old.height, BLOCK):
        h = min(BLOCK, old.height - y)
        for x in range(0, old.width, BLOCK):
            w = min(BLOCK, old.width - x)
            any_change = np.zeros((h, w), dtype=bool)
            any_valid = np.zeros((h, w), dtype=bool)
            block_diff = np.zeros((h, w), dtype=np.float64)
            for b in range(old.bands):
                a = ds_old.GetRasterBand(b + 1).ReadAsArray(x, y, w, h)
                n = ds_new.GetRasterBand(b + 1).ReadAsArray(x, y, w, h)
                va, vn = _valid(a, nd_old[b]), _valid(n, nd_new[b])
                both = va & vn
                d = np.where(both, n.astype(np.float64) - a.astype(np.float64), 0.0)
                moved = both & (np.abs(d) > tolerance)
                if b == 0:
                    gained += int(np.count_nonzero(vn & ~va))
                    lost += int(np.count_nonzero(va & ~vn))
                any_change |= moved | (va != vn)
                any_valid |= va | vn
                if old.bands == 1:
                    block_diff = d
                else:
                    block_diff = np.maximum(block_diff, np.abs(d))
            block_diff[~any_change] = 0
            # a pixel that only gained or lost data still has to show up
            block_diff[any_change & (block_diff == 0)] = np.float32(np.finfo(np.float32).tiny)
            out_band.WriteArray(block_diff.astype(np.float32), x, y)
            mask.GetRasterBand(1).WriteArray(any_change.astype(np.uint8), x, y)

            n_changed = int(np.count_nonzero(any_change))
            changed += n_changed
            valid += int(np.count_nonzero(any_valid))
            if n_changed:
                vals = block_diff[any_change & (np.abs(block_diff) > np.finfo(np.float32).tiny)]
                if vals.size:
                    dmin, dmax = min(dmin, float(vals.min())), max(dmax, float(vals.max()))
                    abs_sum += float(np.abs(vals).sum())
                r = np.nonzero(any_change.any(axis=1))[0]
                c = np.nonzero(any_change.any(axis=0))[0]
                box = (y + r[0], y + r[-1], x + c[0], x + c[-1])
                rows = (min(rows[0], box[0]), max(rows[1], box[1])) if rows else box[:2]
                cols = (min(cols[0], box[2]), max(cols[1], box[3])) if cols else box[2:]
    out_band.FlushCache()
    out = None

    result.compared = True
    result.changed_pixels, result.valid_pixels = changed, valid
    result.gained_pixels, result.lost_pixels = gained, lost
    if changed:
        result.diff_path = diff_path
        result.min_diff = dmin if dmin != np.inf else 0.0
        result.max_diff = dmax if dmax != -np.inf else 0.0
        result.mean_abs_diff = abs_sum / changed
        gt = old.geotransform
        xs = (gt[0] + cols[0] * gt[1], gt[0] + (cols[1] + 1) * gt[1])
        ys = (gt[3] + rows[0] * gt[5], gt[3] + (rows[1] + 1) * gt[5])
        result.bbox = (min(xs), min(ys), max(xs), max(ys))
        result.mask_path = _outline(mask, old, out_dir)
    else:
        gdal.GetDriverByName("GTiff").Delete(diff_path)
    mask = None
    if mask_file:
        gdal.GetDriverByName("GTiff").Delete(mask_file)
    return result


def _outline(mask, grid, out_dir):
    """Polygons around changed pixels, traced on a reduced grid for big rasters."""
    src = mask
    pixels = grid.width * grid.height
    if pixels > MAX_MASK_PIXELS:
        f = (pixels / MAX_MASK_PIXELS) ** 0.5
        src = gdal.Warp("", mask, format="MEM", width=max(1, int(grid.width / f)),
                        height=max(1, int(grid.height / f)), resampleAlg="max")
    path = os.path.join(out_dir, "changed_areas.gpkg")
    drv = ogr.GetDriverByName("GPKG")
    if os.path.exists(path):
        drv.DeleteDataSource(path)
    ds = drv.CreateDataSource(path)
    srs = None
    if grid.srs_wkt:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(grid.srs_wkt)
    lyr = ds.CreateLayer("changed_areas", srs, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("changed", ogr.OFTInteger))
    band = src.GetRasterBand(1)
    gdal.Polygonize(band, band, lyr, 0)
    ds = None
    return path

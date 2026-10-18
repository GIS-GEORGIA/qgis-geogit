# GeoGit: QGIS პროექტის საქაღალდის ვერსიონირება

მთელი პროექტის საქაღალდე (`.qgz`, ვექტორები, რასტრები, სტილები, დოკუმენტები) git-ის კონტროლზე, QGIS-ის პანელიდან, ღილაკებით. დიდი გეო-ფაილები Git LFS-ით ინახება. სერვერად გამოდგება GitHub, Forgejo/Gitea ან ნებისმიერი git სერვერი.

*English below.*

## რას აკეთებს
- **ცვლილებები:** შეცვლილი ფაილების სია ტიპებით, „შენახვა ვერსიად“, ცვლილების გაუქმება
- **სერვერი:** „გამოქვეყნება“ (push), „მოტანა“ (pull), ჩამოტვირთვა (clone), ბრენჩები
- **ისტორია:** ხე (გრაფი) ბრენჩებით, ფერი ავტორის მიხედვით, ფილტრი ავტორით, ფაილის ტიპით და ტექსტით, ერთი ფაილის ისტორია
- **ვექტორის შედარება რუკაზე:** დამატებული (მწვანე), შეცვლილი (ნარინჯისფერი, ძველი საზღვრით), წაშლილი (წითელი)
- **რასტრის შედარება:** სხვაობის ფენა, შეცვლილი უბნების კონტურები, სტატისტიკა, swipe
- **პროექტის შედარება:** ფენები, სტილები, ხელმოწერები, ფენების ხე, CRS, ბეჭდვის layout-ები
- **შერწყმა:** ბრენჩების შერწყმა, კონფლიქტების ფანჯარა, GeoPackage-ის შერწყმა ობიექტებისა და ველების დონეზე
- **დაბლოკვა:** Git LFS-ის დაბლოკვები, რომ ორმა ადამიანმა ერთი ფაილი ერთდროულად არ შეცვალოს
- **ისტორიის ინსტრუმენტები:** ვერსიის მონიშვნა (tag), ცვლილებების ანგარიში (HTML), ფაილის აღდგენა ძველი ვერსიიდან, ვერსიის გაუქმება (revert), ძველი ვერსიის რუკაზე ჩამატება
- ქართული და ინგლისური ინტერფეისი

## მოთხოვნები
- QGIS 3.44 LTR (Qt5) ან QGIS 4.x (Qt6); გატესტილია 3.44, 4.0 და 4.2 ვერსიებზე
- `git`; დიდი ფაილებისა და დაბლოკვისთვის `git-lfs` (`sudo apt install git-lfs`)
- GitHub-ისთვის ერთხელ: `gh auth login && gh auth setup-git`

## საკუთარი სერვერი (Forgejo)
`server/` საქაღალდეში არის Forgejo 15 (LTS) Git LFS-ით და დაბლოკვებით:
```bash
cd server && docker compose up -d
./setup_users.sh                 # geoadmin (ადმინი), nino, giorgi; პაროლები და ტოკენები server/.env.local-ში
```
სერვერი იხსნება მისამართზე http://localhost:3000 (მხოლოდ ამ კომპიუტერიდან). production-ისთვის საჭიროა HTTPS reverse proxy და `ROOT_URL`-ის შეცვლა `docker-compose.yml`-ში.

## დეველოპმენტი
```bash
scripts/dev_install.sh                                   # QGIS პროფილში ბმულის შექმნა
python3 scripts/package.py                               # dist/geogit-<ვერსია>.zip პლაგინების რეპოსთვის
python3 -m unittest discover -s tests                    # unit ტესტები
QT_QPA_PLATFORM=offscreen python3 tests/smoke_qgis.py    # პანელის ტესტი PyQGIS-ში
QT_QPA_PLATFORM=offscreen python3 tests/smoke_diff.py    # ვექტორის ცვლილებები რუკაზე
QT_QPA_PLATFORM=offscreen python3 tests/smoke_raster.py  # რასტრის ცვლილებები და swipe
QT_QPA_PLATFORM=offscreen python3 tests/smoke_project.py # პროექტის ცვლილებები და დაბლოკვის ნიშნები
QT_QPA_PLATFORM=offscreen python3 tests/smoke_merge.py   # pull კონფლიქტით, შერწყმა და დასრულება
QT_QPA_PLATFORM=offscreen python3 tests/smoke_tools.py   # tag, ანგარიში, აღდგენა, revert, ძველი ვერსია რუკაზე
python3 tests/server_e2e.py                              # ორი მომხმარებელი ლოკალურ Forgejo-ზე
scripts/test_in_docker.sh 3.44 4.0 4.2                   # ყველა ტესტი QGIS-ის ოფიციალურ docker image-ებში
```

---

# GeoGit: version control for QGIS project folders

Puts a whole project folder (`.qgz`, vector and raster layers, styles, documents) under git, with Git LFS for large geodata, and drives it from a QGIS panel.

## Features
- **Changes:** changed files by type, save a version, discard changes
- **Server:** publish (push), get updates (pull), download (clone), branches; GitHub, Forgejo/Gitea or any git server
- **History:** graph with branches and merges, colour per author, filters by author, file type and text, history of one file
- **Vector comparison on the map:** added (green), modified (orange, with the old shape), deleted (red)
- **Raster comparison:** difference layer, outlines of changed areas, statistics, swipe tool
- **Project comparison:** layers, styles, labels, layer tree, CRS, print layouts
- **Merge:** merge branches, conflict window, GeoPackages merged feature by feature and attribute by attribute
- **Locks:** Git LFS file locks so two people do not edit one file at the same time
- **History tools:** tag a version, HTML change report, restore a file from an old version, undo a version (revert), add an old version to the map
- Georgian and English interface

## Requirements
QGIS 3.44 LTR (Qt5) or QGIS 4.x (Qt6), tested on 3.44, 4.0 and 4.2; `git`, and `git-lfs` for large files and locks.

## Own server
`server/` holds a Forgejo 15 (LTS) setup with Git LFS and locks: `docker compose up -d`, then `./setup_users.sh`.

License: GPL-3.0-or-later

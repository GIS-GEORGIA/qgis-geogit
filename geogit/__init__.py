def classFactory(iface):
    from .plugin import GeoGitPlugin
    return GeoGitPlugin(iface)

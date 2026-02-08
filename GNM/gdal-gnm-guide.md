# GDAL GNM (Geographic Network Model): What It's For and How It Works

## Origin and Purpose

GNM was developed by Mikhail Gusev during Google Summer of Code 2014, mentored by NextGIS, and integrated into GDAL 2.1 via [RFC 48](https://gdal.org/en/stable/development/rfc/rfc48_geographical_networks_support.html). The motivation was to give GDAL a network abstraction layer analogous to what OGR provides for vector formats — a uniform interface for creating, managing, and analysing geographic networks regardless of the underlying storage format.

## Target Use Cases

GNM is designed for **infrastructure and utility networks** — systems where geographic features have explicit topological connections:

- **Water/sewerage pipe networks** — the canonical example in the GDAL autotest and tutorial. Wells (nodes) connected by pipes (edges), with pumps added dynamically. Find the shortest path water must travel from source to tap, identify connected components (which wells are served by which mains).

- **Road networks** — intersections as nodes, road segments as edges, with costs representing distance or travel time. Dijkstra shortest path and K-shortest-paths (Yen's algorithm) for routing.

- **Electrical grids** — substations, transformers (nodes) connected by transmission lines (edges). Connected components analysis reveals which parts of the grid are isolated if a line is severed.

- **Telecommunications** — exchanges/towers connected by fibre or radio links.

- **River/drainage networks** — confluences as nodes, channel segments as edges. Directed graph with flow direction.

The key selling point was supposed to be **format abstraction**: just as OGR lets you read shapefiles, PostGIS, GeoPackage etc. through one API, GNM was intended to provide a single interface for pgRouting, OSRM, GraphHopper, Oracle Spatial Networks, SpatiaLite VirtualNetwork, and GML topology. In practice, only the "generic" (GNMFile) format — which stores network topology as extra system layers alongside regular OGR layers — was ever implemented. The planned drivers for pgRouting, OSRM etc. never materialised.

## Reality Check: Adoption

GNM has seen very limited adoption. A few indicators:

- The QGIS plugins that wrap it ([QNetwork](https://plugins.qgis.org/plugins/qnetwork/), [GDAL GNM for Processing](https://plugins.bruy.me/processing-gnm.html)) exist but are niche.
- Bug reports are sparse — [trac #6876](https://trac.osgeo.org/gdal/ticket/6876) from ~2017 about `ConnectPointsByLines` failing on some datasets, with the response essentially being "you need to pre-process your data."
- The autotest suite has one test file (`autotest/gnm/gnm_test.py`) with two tiny test shapefiles (`pipes` and `wells`, ~7 KB total).
- No new GNM drivers have been added since the initial GSoC.
- Most people doing network analysis on geospatial data in Python use NetworkX, OSMnx, or pgRouting directly.

That said, it *is* there, it *does* work for its narrow scope, and it's an interesting piece of GDAL that almost nobody knows about.

## The API Surface

### Two class hierarchies via `osgeo.gnm`

```
Network (GNMNetwork)          — abstract base, inherits from GDALDataset
  └─ GenericNetwork (GNMGenericNetwork) — concrete implementation using OGR layers
```

`Network` is what you get when you open an existing network with `gdal.OpenEx()` using `gdal.OF_GNM`. `GenericNetwork` is what you get when you create one via the `GNMFile` driver, and it adds the topology-building and rule methods.

### Key methods on `Network`

| Method | Purpose |
|--------|---------|
| `GetPath(start_gfid, end_gfid, algorithm, options)` | Shortest path (Dijkstra) or K-shortest-paths (Yen). Returns an OGRLayer of features along the path. |
| `GetFeatureByGlobalFID(gfid)` | Look up any feature across all layers by its network-wide unique ID. |
| `CreateLayer(name, srs, geom_type)` | Standard GDALDataset method — add a "class layer" to the network. |
| `CopyLayer(src_layer, new_name)` | Copy an OGR layer into the network (features get registered and assigned GFIDs). |
| `DisconnectAll()` | Wipe the network graph. |

### Key methods on `GenericNetwork` (extends `Network`)

| Method | Purpose |
|--------|---------|
| `ConnectFeatures(src_fid, tgt_fid, connector_fid, cost, inv_cost, direction)` | **Manual** topology: connect two features via a third (the edge). If `connector_fid` is -1, a virtual edge is created. |
| `DisconnectFeatures(src_fid, tgt_fid, connector_fid)` | Remove a specific connection. |
| `ReconnectFeatures(...)` | Change connection attributes (cost, direction). |
| `ConnectPointsByLines(layer_list, tolerance, cost, inv_cost, direction)` | **Automatic** topology building: finds line endpoints near points within tolerance and creates connections. |
| `CreateRule(rule_string)` | Define connection constraints, e.g. "ALLOW CONNECTS pipes WITH wells" |
| `GetRules()` | List current rules. |
| `DeleteRule(rule_string)` / `DeleteAllRules()` | Remove rules. |
| `ChangeBlockState(gfid, is_blocked)` | Block/unblock a feature — blocked features are excluded from path calculations. |
| `ChangeAllBlockState(is_blocked)` | Block/unblock everything. |

### Graph algorithm constants

```python
from osgeo import gnm

gnm.GATDijkstraShortestPath  # Dijkstra single shortest path
gnm.GATKShortestPath         # Yen's K-shortest-paths
gnm.GATConnectedComponents   # Find connected components
```

### Direction constants

```python
gnm.GNM_EDGE_DIR_BOTH       # Undirected (bidirectional)
gnm.GNM_EDGE_DIR_SRCTOTGT   # Directed: source → target only
gnm.GNM_EDGE_DIR_TGTTOSRC   # Directed: target → source only
```

## What the Autotest Does (Reconstructed)

The autotest (`autotest/gnm/gnm_test.py`) uses two shapefiles in `autotest/gnm/data/`:

- **`wells.shp`** — point features representing water wells
- **`pipes.shp`** — line features representing pipes connecting them

These form a small water distribution network. The test sequence exercises the full lifecycle:

### Phase 1: Create the network

```python
from osgeo import gdal, ogr, gnm

# Register all drivers
gdal.AllRegister()

# Get the GNMFile driver
drv = gdal.GetDriverByName("GNMFile")

# Create a new generic network backed by shapefiles
# Key creation options:
#   GNM_MD_NAME   — network name (also becomes the directory name)
#   GNM_MD_SRS    — spatial reference
#   GNM_MD_DESCR  — description
#   GNM_MD_FORMAT — underlying OGR format for storage
ds = drv.Create(
    "tmp/test_gnm",  # path
    0, 0, 0,         # unused raster dimensions
    gdal.GDT_Unknown,
    options=[
        "net_name=test_gnm",
        "net_srs=EPSG:4326",
        "net_description=Test GNM network",
        "FORMAT=ESRI Shapefile",
    ]
)
```

Commentary: `GNMGenericNetwork` inherits from `GDALDataset`. The `Create()` call sets up a directory containing system layers (`_gnm_graph`, `_gnm_meta`, `_gnm_features`) as shapefiles alongside whatever "class layers" you add. The system layers store the incidence list (source GFID, target GFID, connector GFID, costs, direction) and feature registration (GFID ↔ layer name + FID mapping).

### Phase 2: Populate with spatial data

```python
# Open the source data
src_ds = gdal.OpenEx("autotest/gnm/data", gdal.OF_VECTOR)

# Copy layers into the network — this registers every feature
# and assigns each a Global Feature ID (GFID)
ds.CopyLayer(src_ds.GetLayerByName("pipes"), "pipes")
ds.CopyLayer(src_ds.GetLayerByName("wells"), "wells")

src_ds = None  # close source
```

Commentary: After `CopyLayer`, each feature exists in both its "class layer" (pipes or wells) and is registered in the `_gnm_features` system layer with a unique GFID. But there is **no topology yet** — features are just sitting there unconnected.

### Phase 3: Build topology automatically

```python
# Automatic connection: find line endpoints near points
# within the given tolerance and wire them up as graph edges
ds.ConnectPointsByLines(
    ["pipes", "wells"],  # layers to consider
    0.000001,            # tolerance (in SRS units — degrees here)
    1.0,                 # forward cost
    1.0,                 # inverse cost  
    gnm.GNM_EDGE_DIR_BOTH  # undirected edges
)
```

Commentary: This is the workhorse. For each pipe (line feature), the algorithm checks whether the line's start point and end point fall within `tolerance` of any well (point feature). If both ends match a well, it creates a connection: well₁ ←→ pipe ←→ well₂. The pipe feature serves as the "connector" (edge) between the two wells (nodes).

The tolerance caveat from the C++ docs is important: it constructs a square envelope (not a circle) around each endpoint, so the effective search area is `2 * tolerance` on each side. For geographic CRS (degrees), small values like 0.000001 correspond to roughly 0.1m at mid-latitudes.

### Phase 4: Define rules (optional constraints)

```python
# Rules constrain which layer types can connect
ds.CreateRule("ALLOW CONNECTS pipes WITH wells")

# Check rules
rules = ds.GetRules()
# Returns something like: ['ALLOW CONNECTS pipes WITH wells']
```

Commentary: Rules are a business-logic layer. If a rule says "pipes can only connect to wells," then attempting to connect a pipe to another pipe would fail. In practice the test creates and then deletes rules to verify the mechanism works.

### Phase 5: Manual topology editing

```python
# Connect two specific features by GFID
# Args: source_gfid, target_gfid, connector_gfid, cost, inv_cost, direction
#
# connector_gfid = -1 means "create a virtual edge" (no physical feature)
ds.ConnectFeatures(0, 3, -1, 5.0, 5.0, gnm.GNM_EDGE_DIR_BOTH)

# Disconnect them
ds.DisconnectFeatures(0, 3, -1)

# Reconnect with different cost
ds.ReconnectFeatures(0, 3, -1, 2.0, 8.0, gnm.GNM_EDGE_DIR_SRCTOTGT)
```

Commentary: Manual connection uses GFIDs, not layer-local FIDs. The "virtual edge" concept (connector_gfid = -1) lets you create graph edges where no physical line feature exists — e.g., connecting two wells that are close together without an explicit pipe in the data.

### Phase 6: Path analysis

```python
# Reopen the network for analysis
net = gdal.OpenEx("tmp/test_gnm", gdal.OF_GNM)

# Dijkstra shortest path from GFID 61 to GFID 1
path_layer = net.GetPath(61, 1, gnm.GATDijkstraShortestPath)

# The result is an OGRLayer containing the features along the path
# (both node and edge features), ordered from start to end
if path_layer is not None:
    for feat in path_layer:
        print(feat.GetFID(), feat.GetGeometryRef().ExportToWkt())
    
    # Check feature count on the path
    assert path_layer.GetFeatureCount() > 0

# K-shortest-paths using Yen's algorithm
kpaths_layer = net.GetPath(61, 1, gnm.GATKShortestPath, ["num_paths=3"])

# Connected components
components = net.GetPath(0, 0, gnm.GATConnectedComponents)
```

Commentary: `GetPath()` returns an in-memory `OGRLayer`. For Dijkstra, the features in the layer represent the actual path — alternating between node features (wells) and edge features (pipes). For K-shortest-paths, K paths are returned (the `num_paths` option controls K). For connected components, the result layer groups features by their component.

### Phase 7: Blocking features

```python
# Block a feature — it becomes impassable
ds.ChangeBlockState(50, True)

# Now shortest path must route around the blocked feature
path_layer2 = net.GetPath(61, 1, gnm.GATDijkstraShortestPath)
# This path will differ from the unblocked path (or be None if no route exists)

# Unblock
ds.ChangeBlockState(50, False)

# Block everything, then unblock
ds.ChangeAllBlockState(True)
ds.ChangeAllBlockState(False)
```

Commentary: Blocking is the closest thing GNM has to dynamic network modelling. You can simulate pipe bursts, road closures, etc. by blocking features and re-running path analysis. The autotest verifies that blocking changes the result.

### Phase 8: Cleanup

```python
# Disconnect all topology
ds.DisconnectAll()

# Delete the network
drv.Delete("tmp/test_gnm")
```

## The Command-Line Utilities

GNM also has two CLI tools that mirror the Python API:

**`gnmmanage`** — create/delete networks, build topology:
```bash
# Create a network from shapefiles
gnmmanage create -f "ESRI Shapefile" -t_srs "EPSG:4326" \
  -dsco "net_name=my_pipes" ./network_dir

# Import layers
gnmmanage import ./data/pipes.shp ./network_dir
gnmmanage import ./data/wells.shp ./network_dir

# Auto-connect
gnmmanage autoconnect 0.000001 ./network_dir
```

**`gnmanalyse`** — run graph algorithms:
```bash
# Dijkstra shortest path between GFIDs 61 and 1
gnmanalyse dijkstra 61 1 -ds result.shp -f "ESRI Shapefile" ./network_dir

# K-shortest-paths (3 paths)
gnmanalyse kpaths 61 1 3 ./network_dir

# Connected components
gnmanalyse resource ./network_dir
```

## Why It Didn't Take Off

A few factors:

1. **Only one driver was ever implemented** (GNMFile/generic format using OGR layers). The planned pgRouting, OSRM, GraphHopper, SpatiaLite, Oracle Spatial, and GML topology drivers never appeared. Without these, the "format abstraction" value proposition is moot.

2. **`ConnectPointsByLines` is fragile.** It requires your point features to be geometrically coincident (within tolerance) with line endpoints. Real-world data rarely satisfies this without preprocessing. The algorithm doesn't split lines at intermediate points, so a pipe passing *through* a well without ending there won't create a connection.

3. **Limited analysis.** Dijkstra, K-shortest-paths, and connected components cover the basics, but tools like NetworkX, igraph, or pgRouting offer vastly more: betweenness centrality, minimum spanning trees, flow algorithms, isochrones, turn restrictions, time-dependent costs, etc.

4. **The ecosystem moved on.** OSMnx + NetworkX became the standard for Python network analysis on geospatial data. pgRouting dominates the database-integrated routing space. OSRM and Valhalla handle production routing. GNM occupies an awkward middle ground — more capable than simple graph construction but far less capable than dedicated tools.

5. **Maintenance.** The GSoC student (Mikhail Gusev) and mentor (NextGIS/Dmitry Baryshnikov) moved on. Without ongoing development, the code has been essentially static since ~2016.

## Where GNM Fits Today

If you already have GDAL installed and need to do *simple* shortest-path analysis on vector data without pulling in additional dependencies, GNM works. It's particularly suited to small utility networks (water, gas, electrical) where the data is clean and the analysis needs are basic. The ability to block features and re-analyse is genuinely useful for "what happens if this pipe breaks?" scenarios.

For anything more complex, you're better served by exporting your OGR features to NetworkX, igraph, or a PostGIS/pgRouting database.

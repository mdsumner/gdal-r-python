# Real xarray, virtual xarray, and the format that was already there

## The pun

"Virtual xarray" is two things wearing the same name, and most of the friction in this corner of the ecosystem traces back to the moment you stop noticing.

One is the working-set xarray: the in-memory container with dims, coords, and attrs that you slice, interp, rolling-mean, groupby. It models a logical array. It doesn't care where the numbers came from. It's designed to be fluid — to compose operations without committing to any particular storage layout.

The other is the manifest-wrapper xarray: the same container, but its `.data` is backed by a lazy reference to on-disk bytes that hasn't materialised yet. The contents haven't been computed; a recipe has. The Dataset is serving as a human-accessible view onto something that's really a manifest of chunks, references, URLs, byte ranges.

Both get printed identically at the REPL. Both respond to `ds.isel(time=slice(0, 5))`. The moment you hit an operation that forces the working-set model — `.rolling`, `.interp`, `.coarsen`, anything that rechunks — the manifest evaporates. What was a declarative recipe becomes an opaque dask graph. Serializing it back to a manifest format (kerchunk parquet, Zarr V3 manifest, Icechunk, VRT) requires rediscovering what the manifest wrapper used to know.

Tom Nicholas has been making this point loudly: treating these as one thing is a type-level error. xarray's abstraction doesn't distinguish them. The user does, and gets away with it most of the time, until they don't.

## What concat loses

The friction shows up concretely when you try to write a manifest from an xarray that's been through concat. `xr.open_mfdataset(paths)` does this invisibly; every downstream `xr.concat` does it explicitly. In either case, the per-file provenance — *this chunk came from that file at that offset* — doesn't survive as a first-class thing on the resulting Dataset.

You can still see the file boundaries *if* the inputs happened to have on-disk chunks that match file boundaries. For OISST daily files (one timestep per file), you're fine: each file is one chunk, the concat-product's `chunks[0]` is a tuple of ones, length equals number of files. Legible.

For BRAN monthly files chunked 1-per-day on disk, you're not. Each file contributes 30-ish chunks of size 1, the concat flattens them, and `chunks[0]` is now a long run of ones with no boundary markers. The information is gone. Not hidden, gone. xarray doesn't track "chunk belongs to file".

This isn't a bug in concat. It's concat being honest about what concat is: take N arrays, make one. The provenance that *some* of those elements came from file A and *others* came from file B is below the abstraction level xarray operates at.

And here's the workaround that makes the whole story click: open with `chunks={concat_dim: -1}`. That tells dask to ignore the on-disk chunking and use one chunk spanning the whole concat dim per file. After concat, `chunks[0]` is exactly the per-file sizes — `(31, 28, 31, 30, 31, ...)` for monthly BRAN. The file boundaries are now visible through the public xarray API, because chunk structure became your boundary carrier.

## The rule nobody writes down

This suggests a real invariant. Chunks aren't just a performance knob — they're the sole place in a dask-backed xarray Dataset where per-file structure can live without help from private internals.

**A chunk structure that matches file boundaries is what makes a dask-backed xarray Dataset round-trippable to a manifest format.**

Every manifest writer in the ecosystem wants this. Kerchunk wants it. VirtualiZarr wants it. xvrt wants it. None of them currently document it as a reader-side prerequisite, and the defaults don't produce it — NetCDF's default is inherit-from-disk (great for I/O, bad for serialization), Zarr's is chunk-as-stored. So everyone rediscovers, from their own angle, that a manifest writer has to reach back into the files it's describing, because the user's `open_mfdataset` call threw away what the writer needs.

The rule belongs in the shared ecosystem note that doesn't exist yet. In the meantime it belongs in xvrt's README, kerchunk's FAQ, VirtualiZarr's getting-started. The phenomenon is one; the framings are many.

## The format that was already there

Here's the part that's easy to miss if you grew up in the Zarr world: GDAL has had a manifest format for virtual datasets since before most of the current tooling existed. VRT has been shipping since roughly 2003 for classic raster; the multidim extension landed around 2019. It is declarative XML. It stores per-source `<SourceFilename>`. It composes via `<DestSlab>`. It supports derivations via `<DerivedArray>`. It parses in constant time because it's text. It round-trips byte-for-byte through `gdal mdim convert`. Every property the Zarr-world manifest formats are grasping toward, VRT has had.

It's been hiding in plain sight as "GDAL's internal XML". Which is underselling it. `gdalvrt.xsd` is a public schema. Anyone can emit it. Anyone can read it. GDAL is the reference implementation; there's no requirement to go through GDAL to produce or consume it. It's a lingua franca for heterogeneous-file mdim arrays, in the same sense that kerchunk parquet is a lingua franca for heterogeneous chunk references.

The Zarr community didn't fail to notice — they came from a different set of constraints (object storage, cloud-native access patterns, Python-first tools) and built their manifest layer on top of Zarr's chunk addressing rather than on top of per-file references. Both solve the same underlying problem. Both are lazy, composable, serializable, declarative. One is twenty years older and better known in the raster / oceanography / GDAL-PSC orbit; the other is five years old and better known in the xarray / cloud-native / pangeo orbit. Treating them as peers is probably the more useful framing than treating them as competitors.

## The asymmetry

There's an asymmetry between the two directions that explains why the xarray-centric world has been slower to adopt VRT, and why the GDAL-centric world has been quieter about xarray's lossy concat.

VRT → xarray is lossless when the reader is built right. `gdx` opens a VRT and produces a dask-backed xarray Dataset where each source becomes one chunk — the chunk structure exactly mirrors the VRT's `<Source>` list. Every other manifest writer would now see the file boundaries through `chunks`. The manifest is faithfully represented at the xarray API level, precisely because the reader knew to set chunks to match sources. The rule stated above becomes a reader-implementation detail, and everything works.

xarray → VRT is lossy unless the reader was already disciplined. If the user opened their files with default chunks and then ran xvrt, the per-file sizes aren't there and xvrt has to rediscover them by opening the files again. If they opened with `chunks={concat_dim: -1}`, the sizes are in `ds["var"].chunks[0]` and xvrt's step 3 reads them for free.

So the asymmetry isn't in the formats. VRT and Zarr manifests are equally expressive. The asymmetry is in what a typical user's open call produces: for VRT → xarray the reader controls it and does the right thing; for arbitrary-source → xarray → VRT the user controls it and usually doesn't know the rule.

## What an honest fix would look like

The upstream answer isn't "teach xarray to preserve file boundaries through concat" — that breaks the working-set abstraction, which is doing real work. The answer is what Tom and the VirtualiZarr folks are already pushing: two types, two roles, explicit conversion.

The manifest-wrapper object would have stable semantics: operations that preserve manifest structure (sel, isel, concat-of-manifests, merge) return a manifest-wrapper; operations that would destroy it (interp, rolling, coarsen) either error, or return a working-set xarray and make you acknowledge the conversion. Serialization writes the manifest directly. No rediscovery needed because nothing was discarded.

The working-set xarray stays as-is. You get to it explicitly via `.compute()` or `.load()` or `.to_working_set()`. The pun is retired.

VRT slots into this cleanly. `gdx.open_mdim_vrt(path)` returns a manifest-wrapper; `ds.to_mdim_vrt(path)` is the symmetric serialization; operations that preserve structure compose; operations that don't force a conversion. Same story for kerchunk, VirtualiZarr, Zarr V3 manifests, Icechunk. The manifest type is the peer citizen the Zarr V3 spec has been circling.

Until that lands, xvrt lives in the liminal zone: it accepts a working-set xarray and does its best to reconstruct the manifest intent, with helpful errors when reconstruction is ambiguous. Not a permanent home, but a useful one while the upstream types sort themselves out.

## Coda: names

One unworthy-but-true observation. If VRT had been called "NetCDF Virtual Manifest" or "Cloud-Optimized NetCDF Catalog" it would probably have a pangeo contingent by now. "GDAL VRT" sounds like driver-internal plumbing, so the xarray crowd skates past it en route to kerchunk. Names are destiny, and this one has cost the format five years of adoption.

The format doesn't care. It's still there, still working, still solving the problem. And once you've seen that xvrt is a one-way bridge on top of it, and that `gdx` is the other-way bridge, and that together they're enacting the manifest-wrapper pattern that the xarray core still doesn't have a type for — the whole landscape rearranges itself. There's real xarray, there's virtual xarray, and there's the text file that's been quietly doing the missing intermediate since before either of them had a name.

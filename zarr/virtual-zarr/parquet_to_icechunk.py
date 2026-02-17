"""
Kerchunk Parquet → Icechunk converter

Two modes:
1. Kerchunk-style: path, offset, size columns with implicit row-major chunk indexing
2. Explicit-style: variable, i0, i1, ..., path, offset, length with explicit indices

The second mode is what rustycogs could emit for maximum flexibility.
"""

import pandas as pd
import numpy as np
import icechunk
import zarr
import shutil
from pathlib import Path


def flat_to_chunk_index(flat_idx: int, n_chunks_per_dim: tuple) -> tuple:
    """Convert flat row index to chunk tuple (row-major order)."""
    coords = []
    remaining = flat_idx
    for n in reversed(n_chunks_per_dim):
        coords.append(remaining % n)
        remaining //= n
    return tuple(reversed(coords))


def kerchunk_parquet_to_icechunk(
    refs_df: pd.DataFrame,
    shape: tuple,
    chunks: tuple,
    dtype: str,
    dims: list[str],
    variable_name: str = "data",
) -> list:
    """
    Convert Kerchunk-style parquet (implicit indexing) to VirtualChunkSpecs.
    
    Args:
        refs_df: DataFrame with columns: path, offset, size (or length)
        shape: Array shape
        chunks: Chunk sizes
        dtype: Data type string
        dims: Dimension names
        variable_name: Name for the variable
        
    Returns:
        List of VirtualChunkSpec objects
    """
    n_chunks_per_dim = tuple((s + c - 1) // c for s, c in zip(shape, chunks))
    
    # Handle column name variations
    size_col = "size" if "size" in refs_df.columns else "length"
    
    specs = []
    for i, row in refs_df.iterrows():
        if pd.isna(row["path"]):
            continue  # Skip missing chunks
            
        chunk_idx = flat_to_chunk_index(i, n_chunks_per_dim)
        specs.append(icechunk.VirtualChunkSpec(
            index=chunk_idx,
            location=row["path"],
            offset=int(row["offset"]),
            length=int(row[size_col]),
        ))
    
    return specs


def explicit_parquet_to_icechunk(refs_df: pd.DataFrame) -> dict[str, list]:
    """
    Convert explicit-index parquet to VirtualChunkSpecs per variable.
    
    Expected columns: variable, i0, i1, ..., path, offset, length
    
    Returns:
        Dict mapping variable name to list of VirtualChunkSpecs
    """
    result = {}
    
    # Find index columns (i0, i1, i2, ...)
    idx_cols = sorted([c for c in refs_df.columns if c.startswith("i") and c[1:].isdigit()])
    
    for var in refs_df["variable"].unique():
        var_df = refs_df[refs_df["variable"] == var]
        specs = []
        
        for _, row in var_df.iterrows():
            if pd.isna(row["path"]):
                continue
                
            chunk_idx = tuple(int(row[c]) for c in idx_cols)
            specs.append(icechunk.VirtualChunkSpec(
                index=chunk_idx,
                location=row["path"],
                offset=int(row["offset"]),
                length=int(row["length"]),
            ))
        
        result[var] = specs
    
    return result


def create_icechunk_from_flat_parquet(
    parquet_path: str,
    metadata: dict,
    output_path: str,
    url_prefix: str,
    coords: dict = None,
) -> str:
    """
    Create Icechunk store from flat parquet refs.
    
    Args:
        parquet_path: Path to parquet file with refs
        metadata: Dict with per-variable metadata:
            {
                "sst": {"shape": [...], "chunks": [...], "dtype": "float32", "dims": [...]},
                ...
            }
        output_path: Where to create Icechunk store
        url_prefix: URL prefix for virtual chunk container (e.g., "s3://bucket/")
        coords: Optional dict of coordinate arrays {"time": [...], "lat": [...], ...}
        
    Returns:
        Snapshot ID
    """
    # Read parquet
    df = pd.read_parquet(parquet_path)
    
    # Determine format (implicit vs explicit)
    has_variable_col = "variable" in df.columns
    has_index_cols = any(c.startswith("i") and c[1:].isdigit() for c in df.columns)
    
    # Setup Icechunk
    shutil.rmtree(output_path, ignore_errors=True)
    storage = icechunk.local_filesystem_storage(output_path)
    config = icechunk.RepositoryConfig.default()
    
    # Determine store type from url_prefix
    if url_prefix.startswith("s3://"):
        store = icechunk.s3_store()
    elif url_prefix.startswith("http"):
        store = icechunk.http_store()
    else:
        store = icechunk.local_filesystem_store()
    
    config.set_virtual_chunk_container(icechunk.VirtualChunkContainer(
        url_prefix=url_prefix,
        store=store,
    ))
    
    repo = icechunk.Repository.create(storage=storage, config=config)
    session = repo.writable_session("main")
    ic_store = session.store
    
    # Create zarr structure
    root = zarr.open_group(ic_store, mode="w", zarr_format=3)
    
    # Create coordinates if provided
    if coords:
        for name, values in coords.items():
            arr = np.asarray(values)
            root.create_array(name, data=arr, dimension_names=[name])
    
    # Process refs
    if has_variable_col and has_index_cols:
        # Explicit format
        var_specs = explicit_parquet_to_icechunk(df)
        for var, specs in var_specs.items():
            meta = metadata[var]
            root.create_array(
                var,
                shape=meta["shape"],
                chunks=meta["chunks"],
                dtype=meta["dtype"],
                dimension_names=meta["dims"],
            )
            ic_store.set_virtual_refs(var, specs, validate_containers=False)
    else:
        # Implicit format (single variable)
        if len(metadata) != 1:
            raise ValueError("Implicit format requires exactly one variable in metadata")
        var = list(metadata.keys())[0]
        meta = metadata[var]
        
        specs = kerchunk_parquet_to_icechunk(
            df, 
            tuple(meta["shape"]),
            tuple(meta["chunks"]),
            meta["dtype"],
            meta["dims"],
            var,
        )
        
        root.create_array(
            var,
            shape=meta["shape"],
            chunks=meta["chunks"],
            dtype=meta["dtype"],
            dimension_names=meta["dims"],
        )
        ic_store.set_virtual_refs(var, specs, validate_containers=False)
    
    snapshot_id = session.commit("Created from flat parquet")
    return snapshot_id


# =============================================================================
# Example usage
# =============================================================================

if __name__ == "__main__":
    import tempfile
    
    # Example 1: Implicit indexing (kerchunk-style)
    print("=== Example 1: Implicit indexing ===")
    implicit_df = pd.DataFrame({
        "path": ["s3://bucket/f.nc"] * 4,
        "offset": [100, 200, 300, 400],
        "size": [100, 100, 100, 100],
    })
    print(implicit_df)
    
    specs = kerchunk_parquet_to_icechunk(
        implicit_df,
        shape=(2, 2),
        chunks=(1, 1),
        dtype="float32",
        dims=["lat", "lon"],
    )
    print(f"\nConverted to {len(specs)} VirtualChunkSpecs:")
    for s in specs:
        print(f"  {s.index} -> {s.location}[{s.offset}:{s.offset+s.length}]")
    
    # Example 2: Explicit indexing (ideal flat format)
    print("\n=== Example 2: Explicit indexing ===")
    explicit_df = pd.DataFrame({
        "variable": ["sst", "sst", "sst", "sst", "anom", "anom", "anom", "anom"],
        "i0": [0, 0, 1, 1, 0, 0, 1, 1],
        "i1": [0, 1, 0, 1, 0, 1, 0, 1],
        "path": ["s3://bucket/f.nc"] * 8,
        "offset": [100, 200, 300, 400, 500, 600, 700, 800],
        "length": [100] * 8,
    })
    print(explicit_df)
    
    var_specs = explicit_parquet_to_icechunk(explicit_df)
    print(f"\nConverted to specs per variable:")
    for var, specs in var_specs.items():
        print(f"  {var}: {len(specs)} specs")
        for s in specs[:2]:
            print(f"    {s.index} -> {s.location}[{s.offset}:{s.offset+s.length}]")
    
    # Example 3: Full pipeline
    print("\n=== Example 3: Full pipeline ===")
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        parquet_path = f.name
    
    explicit_df.to_parquet(parquet_path)
    
    metadata = {
        "sst": {"shape": [2, 2], "chunks": [1, 1], "dtype": "float32", "dims": ["lat", "lon"]},
        "anom": {"shape": [2, 2], "chunks": [1, 1], "dtype": "float32", "dims": ["lat", "lon"]},
    }
    
    coords = {
        "lat": [-45, 45],
        "lon": [0, 180],
    }
    
    output_path = "/tmp/test_flat_to_icechunk"
    snapshot_id = create_icechunk_from_flat_parquet(
        parquet_path,
        metadata,
        output_path,
        url_prefix="s3://bucket/",
        coords=coords,
    )
    print(f"Created Icechunk store: {output_path}")
    print(f"Snapshot: {snapshot_id}")
    
    # Verify
    import xarray as xr
    config = icechunk.RepositoryConfig.default()
    config.set_virtual_chunk_container(icechunk.VirtualChunkContainer(
        url_prefix="s3://bucket/",
        store=icechunk.s3_store(),
    ))
    repo = icechunk.Repository.open(
        storage=icechunk.local_filesystem_storage(output_path),
        config=config,
        authorize_virtual_chunk_access={"s3://bucket/": None},
    )
    ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False)
    print(f"\nOpened as xarray:\n{ds}")

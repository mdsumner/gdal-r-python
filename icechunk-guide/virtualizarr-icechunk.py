"""
VirtualiZarr → Icechunk workflow

Virtualizes NetCDF files into Icechunk stores with virtual chunk references.
Tested with:
  - OISST on Pawsey S3
  - BRAN2023 on NCI Thredds

Key discoveries:
  - Registry uses plain URL prefix, not regex: {"https://example.com": store}
  - Write: Repository.create(storage, config) with VirtualChunkContainer in config
  - Read: Repository.open(storage, config, authorize_virtual_chunk_access={prefix: None})
  - Use session.store for to_icechunk() and xr.open_zarr()

Requirements:
  pip install "virtualizarr[hdf,icechunk]" xarray obstore
  # obspec-utils comes with virtualizarr
"""

import warnings
from pathlib import Path

import xarray as xr

warnings.filterwarnings("ignore", message="Numcodecs codecs are not in the Zarr version 3 specification*")
warnings.filterwarnings("ignore", category=FutureWarning)


# =============================================================================
# Configuration
# =============================================================================

# OISST on Pawsey (tested and working)
OISST_CONFIG = {
    "url_prefix": "https://projects.pawsey.org.au/",
    "base_url": "https://projects.pawsey.org.au/idea-10.7289-v5sq8xb5/www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr",
}

# BRAN2023 on NCI Thredds
BRAN_CONFIG = {
    "url_prefix": "https://thredds.nci.org.au/",
    "base_url": "https://thredds.nci.org.au/thredds/fileServer/gb6/BRAN/BRAN2023",
}

# Pawsey S3 for Icechunk store output
PAWSEY_S3_CONFIG = {
    "bucket": "your-bucket-name",  # TODO: set this
    "prefix": "icechunk-stores",
    "endpoint_url": "https://projects.pawsey.org.au",
    "region": None,
}


# =============================================================================
# Core functions
# =============================================================================


def create_registry_and_parser(url_prefix: str):
    """
    Create obstore registry and HDF parser for virtualizing files.
    
    Args:
        url_prefix: Base URL like "https://thredds.nci.org.au"
    """
    from obstore.store import HTTPStore
    from obspec_utils.registry import ObjectStoreRegistry
    from virtualizarr.parsers import HDFParser

    # Plain URL prefix, not regex!
    http_store = HTTPStore.from_url(url_prefix.rstrip("/"))
    registry = ObjectStoreRegistry({url_prefix.rstrip("/"): http_store})
    parser = HDFParser()

    return registry, parser


def create_icechunk_config(url_prefix: str):
    """
    Create Icechunk RepositoryConfig with VirtualChunkContainer.
    
    Args:
        url_prefix: URL prefix for virtual chunk references (include trailing slash)
    """
    import icechunk

    config = icechunk.RepositoryConfig.default()
    container = icechunk.VirtualChunkContainer(
        url_prefix=url_prefix,
        store=icechunk.http_store(),
    )
    config.set_virtual_chunk_container(container)

    return config


def create_local_repo(path: str, url_prefix: str, create_new: bool = True):
    """
    Create or open Icechunk repository on local filesystem.
    """
    import icechunk
    import shutil

    if create_new and Path(path).exists():
        shutil.rmtree(path)

    storage = icechunk.local_filesystem_storage(path)
    config = create_icechunk_config(url_prefix)

    if create_new:
        repo = icechunk.Repository.create(storage=storage, config=config)
    else:
        repo = icechunk.Repository.open(
            storage=storage,
            config=config,
            authorize_virtual_chunk_access={url_prefix: None},  # Anonymous HTTP
        )

    return repo, config


def create_pawsey_repo(bucket: str, prefix: str, url_prefix: str, create_new: bool = True):
    """
    Create or open Icechunk repository on Pawsey S3.
    
    Set environment variables:
        AWS_ACCESS_KEY_ID
        AWS_SECRET_ACCESS_KEY
    """
    import icechunk

    storage = icechunk.s3_storage(
        bucket=bucket,
        prefix=prefix,
        endpoint_url=PAWSEY_S3_CONFIG["endpoint_url"],
        region=PAWSEY_S3_CONFIG["region"],
        from_env=True,
        force_path_style=True,
    )
    config = create_icechunk_config(url_prefix)

    if create_new:
        repo = icechunk.Repository.create(storage=storage, config=config)
    else:
        repo = icechunk.Repository.open(
            storage=storage,
            config=config,
            authorize_virtual_chunk_access={url_prefix: None},
        )

    return repo, config


def virtualize_files(urls: list[str], url_prefix: str, concat_dim: str = "time", combine: str = "nested"):
    """
    Virtualize multiple files into a single virtual dataset.
    
    Args:
        urls: List of URLs to virtualize
        url_prefix: Base URL prefix (e.g., "https://thredds.nci.org.au")
        concat_dim: Dimension to concatenate along
        combine: Combine method ('nested' or 'by_coords')
    """
    from virtualizarr import open_virtual_dataset, open_virtual_mfdataset

    registry, parser = create_registry_and_parser(url_prefix)

    if len(urls) == 1:
        vds = open_virtual_dataset(urls[0], registry=registry, parser=parser)
    else:
        vds = open_virtual_mfdataset(
            urls,
            registry=registry,
            parser=parser,
            concat_dim=concat_dim,
            combine=combine,
        )

    return vds


def write_to_icechunk(vds: xr.Dataset, repo, message: str = "Virtualized dataset"):
    """
    Write virtual dataset to Icechunk repository.
    """
    session = repo.writable_session("main")
    vds.virtualize.to_icechunk(session.store)
    snapshot_id = session.commit(message)
    return snapshot_id


def read_from_icechunk(repo, config, url_prefix: str) -> xr.Dataset:
    """
    Read dataset from Icechunk repository.
    
    Note: Must reopen repo with authorize_virtual_chunk_access for reads.
    """
    import icechunk

    # For reads, we need authorize_virtual_chunk_access
    # The repo passed in might be from create(), so we get storage and reopen
    session = repo.readonly_session("main")
    ds = xr.open_zarr(session.store, consolidated=False)
    return ds


# =============================================================================
# OISST workflows
# =============================================================================


def get_oisst_urls(year: int, month: int) -> list[str]:
    """Generate OISST URLs for a given year/month."""
    import calendar
    
    days_in_month = calendar.monthrange(year, month)[1]
    urls = []
    for day in range(1, days_in_month + 1):
        url = f"{OISST_CONFIG['base_url']}/{year}{month:02d}/oisst-avhrr-v02r01.{year}{month:02d}{day:02d}.nc"
        urls.append(url)
    return urls


def test_oisst_single_file(output_path: str = "/tmp/test_oisst.icechunk"):
    """
    Test OISST single file → local Icechunk → read back.
    """
    url = f"{OISST_CONFIG['base_url']}/202506/oisst-avhrr-v02r01.20250630.nc"
    url_prefix = OISST_CONFIG["url_prefix"]

    print(f"Virtualizing: {url}")
    vds = virtualize_files([url], url_prefix)
    print(f"Virtual dataset: {vds.dims}")

    print(f"\nWriting to: {output_path}")
    repo, config = create_local_repo(output_path, url_prefix, create_new=True)
    snapshot_id = write_to_icechunk(vds, repo, "Test OISST single file")
    print(f"Committed: {snapshot_id}")

    print("\nReading back...")
    repo, config = create_local_repo(output_path, url_prefix, create_new=False)
    ds = read_from_icechunk(repo, config, url_prefix)
    print(ds)

    print("\nFetching sample data...")
    sample = ds.sst.isel(time=0, zlev=0, lat=slice(350, 355), lon=slice(500, 505))
    print(f"Lats: {ds.lat.values[350:355]}")
    print(f"SST values:\n{sample.values}")

    return ds


def virtualize_oisst_month(year: int, month: int, output_path: str):
    """
    Virtualize a full month of OISST data.
    """
    urls = get_oisst_urls(year, month)
    url_prefix = OISST_CONFIG["url_prefix"]

    print(f"Virtualizing {len(urls)} OISST files for {year}-{month:02d}")
    vds = virtualize_files(urls, url_prefix, concat_dim="time", combine="nested")
    print(f"Virtual dataset: {vds.dims}")

    print(f"\nWriting to: {output_path}")
    repo, config = create_local_repo(output_path, url_prefix, create_new=True)
    snapshot_id = write_to_icechunk(vds, repo, f"OISST {year}-{month:02d}")
    print(f"Committed: {snapshot_id}")

    return snapshot_id


# =============================================================================
# BRAN2023 workflows
# =============================================================================


def get_bran_urls(variable: str, years: range) -> list[str]:
    """Generate BRAN2023 URLs for a variable across years."""
    urls = []
    for year in years:
        for month in range(1, 13):
            url = f"{BRAN_CONFIG['base_url']}/daily/{variable}_{year}_{month:02d}.nc"
            urls.append(url)
    return urls


def test_bran_single_file(output_path: str = "/tmp/test_bran.icechunk"):
    """
    Test BRAN single file → local Icechunk → read back.
    """
    url = f"{BRAN_CONFIG['base_url']}/daily/ocean_temp_2024_01.nc"
    url_prefix = BRAN_CONFIG["url_prefix"]

    print(f"Virtualizing: {url}")
    print("(This may take a few minutes - BRAN files are large)")
    vds = virtualize_files([url], url_prefix)
    print(f"Virtual dataset: {vds.dims}")

    print(f"\nWriting to: {output_path}")
    repo, config = create_local_repo(output_path, url_prefix, create_new=True)
    snapshot_id = write_to_icechunk(vds, repo, "Test BRAN ocean_temp January 2024")
    print(f"Committed: {snapshot_id}")

    print("\nReading back...")
    repo, config = create_local_repo(output_path, url_prefix, create_new=False)
    ds = read_from_icechunk(repo, config, url_prefix)
    print(ds)

    print("\nFetching sample data...")
    sample = ds.temp.isel(Time=0, st_ocean=0, yt_ocean=slice(700, 705), xt_ocean=slice(1800, 1805))
    print(f"Sample values:\n{sample.values}")

    return ds


def virtualize_bran_variable(variable: str, years: range, output_path: str):
    """
    Virtualize a BRAN variable across multiple years.
    """
    urls = get_bran_urls(variable, years)
    url_prefix = BRAN_CONFIG["url_prefix"]

    print(f"Virtualizing {len(urls)} BRAN files for {variable}")
    print("(This will take a while over HTTP - consider running on NCI)")
    vds = virtualize_files(urls, url_prefix, concat_dim="Time", combine="nested")
    print(f"Virtual dataset: {vds.dims}")

    print(f"\nWriting to: {output_path}")
    repo, config = create_local_repo(output_path, url_prefix, create_new=True)
    snapshot_id = write_to_icechunk(vds, repo, f"BRAN2023 {variable} {years.start}-{years.stop-1}")
    print(f"Committed: {snapshot_id}")

    return snapshot_id


# =============================================================================
# Main
# =============================================================================


if __name__ == "__main__":
    print("=" * 60)
    print("VirtualiZarr → Icechunk Workflow")
    print("=" * 60)
    print()
    print("Available tests:")
    print("  test_oisst_single_file()      - Quick OISST test (~2MB file)")
    print("  test_bran_single_file()       - BRAN test (~500MB file, slow over HTTP)")
    print()
    print("Multi-file workflows:")
    print("  virtualize_oisst_month(2025, 6, '/tmp/oisst_202506.icechunk')")
    print("  virtualize_bran_variable('ocean_temp', range(2024, 2025), '/tmp/bran_temp.icechunk')")
    print()
    print("Run in Python interactive mode and call these functions.")
    print()
    
    # Uncomment to run a test:
    # test_oisst_single_file()
    # test_bran_single_file()

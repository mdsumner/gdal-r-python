# `gdal mdim mosaic` drops the indexing variable's Unit (CF `units`)

## Summary

`gdal mdim mosaic` recreates each dimension's indexing variable as an inline
VRT array and copies its **attributes**, but never propagates its **Unit**
(`GDALMDArray::GetUnit()`). The netCDF multidim driver lifts the CF `units`
attribute (e.g. `days since 1978-01-01 12:00:00`) into the array Unit rather
than exposing it in `GetAttributes()`, so it is silently lost. `gdal mdim
convert` (via `GDALMultiDimTranslate`) preserves it. A time
coordinate that decodes as `datetime64` through a mosaic-produced VRT instead
reads as raw float offsets.

Data arrays are unaffected (they are sourced and keep their Unit); only the
recreated indexing/coordinate variables lose it.

## Reproduce

```
gdal mdim convert oisst-avhrr-v02r01.19810901.nc c.vrt   # time -> datetime64 (units kept)
gdal mdim mosaic  oisst-avhrr-v02r01.19810901.nc m.vrt   # time -> float64 1339.0 (units lost)
```
In the mosaic VRT the `time` array has `<Attribute name="long_name">` but no
`<Unit>`; the convert path keeps the unit.

## Cause

* netCDF driver lifts CF `units` into the array Unit, so it is not in
  `GetAttributes()`:
  `frmts/netcdf/netcdfmultidim.cpp:2187` — `GetAttribute(CF_UNITS)` -> `m_osUnit`.
* mosaic captures only attributes for the indexing variable, and its recreation
  block copies attributes but calls no `SetUnit`:
  `apps/gdalalg_mdim_mosaic.cpp:93` (`desc.attributes = poVar->GetAttributes()`)
  and the copy loop at `:747`. There is no `GetUnit`/`SetUnit` anywhere in
  `gdalalg_mdim_mosaic.cpp`.
* The translate/convert path does consult the indexing variable's unit:
  `apps/gdalmdimtranslate_lib.cpp:1368` (`srcIndexVar->GetUnit()`).
* VRT can hold the unit — it is parsed (`frmts/vrt/vrtmultidim.cpp:1103`,
  `SetUnit`) and serialized (`:2875`, `<Unit>`), so the fix is a straight
  round-trip.

Present since mosaic's first commit. 

## Coverage gap

`autotest/utilities/test_gdalalg_mdim_mosaic.py` already builds indexing
variables with attributes (`axis`, `positive`) but asserts only on
`GetIndexingVariable().Read()` (the values). Neither the mosaic nor the convert
test asserts a Unit round-trip (`grep -n GetUnit` in both: none). A fixture that
sets a unit on the indexing variable and checks it survives sits directly beside
the existing tests and would have caught this.

### Proposed test (for test_gdalalg_mdim_mosaic.py)

```python
def test_gdalalg_mdim_mosaic_preserves_indexing_var_unit(tmp_path):
    # mosaic must preserve the indexing variable's Unit (CF 'units'), which the
    # netCDF driver exposes via GetUnit() rather than GetAttributes().
    # convert/GDALMultiDimTranslate already does; mosaic did not.
    import array

    UNIT = "days since 2000-01-01 00:00:00"

    def make(path, zval, testval):
        with gdal.GetDriverByName("netCDF").CreateMultiDimensional(path) as ds:
            rg = ds.GetRootGroup()
            z = rg.CreateDimension("z", "TEMPORAL", "", 1)
            z_ar = rg.CreateMDArray(
                "z", [z], gdal.ExtendedDataType.Create(gdal.GDT_Float64)
            )
            z_ar.SetUnit(UNIT)  # netCDF writes CF 'units'; reopens as GetUnit()
            z_ar.CreateAttribute(
                "long_name", [1], gdal.ExtendedDataType.CreateString()
            ).WriteString("time")
            z_ar.Write([zval])
            ar = rg.CreateMDArray(
                "test", [z], gdal.ExtendedDataType.Create(gdal.GDT_UInt8)
            )
            ar.Write(array.array("B", [testval]))

    make(tmp_path / "t1.nc", 10.0, 3)
    make(tmp_path / "t2.nc", 20.0, 4)

    # reference: convert preserves the unit (passes today)
    with gdal.Run(
        "mdim", "convert",
        input=tmp_path / "t1.nc", output=tmp_path / "c.vrt", output_format="VRT",
    ) as alg:
        z = alg.Output().GetRootGroup().OpenMDArray("z")
        assert z.GetUnit() == UNIT

    # mosaic must preserve it too (fails before fix, passes after)
    with gdal.Run(
        "mdim", "mosaic",
        input=tmp_path / "t*.nc", output=tmp_path / "m.vrt",
        array="test", output_format="VRT",
    ) as alg:
        idx = alg.Output().GetRootGroup().OpenMDArray("test") \
                 .GetDimensions()[0].GetIndexingVariable()
        assert idx.GetUnit() == UNIT
        # plain attributes already survive:
        assert "long_name" in [a.GetName() for a in idx.GetAttributes()]
```

## Fix (three edits, mirrors the attribute copy already present)

1. `apps/gdalalg_mdim_mosaic.h`, in `struct DimensionDesc` (after `attributes`):
   ```cpp
   std::string osUnit{};   // indexing variable unit (e.g. CF 'units')
   ```

2. `apps/gdalalg_mdim_mosaic.cpp`, in `GetDimensionDesc` (~line 92), capture it:
   ```cpp
   if (poVar)
   {
       desc.attributes = poVar->GetAttributes();
       desc.osUnit = poVar->GetUnit();   // ADD
   }
   ```

3. `apps/gdalalg_mdim_mosaic.cpp`, right after the attribute-copy loop (~line 758),
   replay it onto the recreated variable:
   ```cpp
   if (!desc.osUnit.empty())
       var->SetUnit(desc.osUnit);        // ADD
   ```

`VRTMDArray::SetUnit` exists and serializes to `<Unit>`
(`frmts/vrt/vrtmultidim.cpp:1103`, `:2875`), so no other change is needed.

## Downstream note (R / vrtstack)

Until a fixed GDAL is in hand, `vrtstack`'s recipe step restores the missing
`<Unit>` on coordinate arrays by reading the true unit from one source via
`gdal mdim info` and injecting `<Unit>`; it is idempotent, so it becomes a
harmless no-op once GDAL propagates the unit itself. With the unit present,
gdalxarray CF-decodes `time` to datetimes with nothing to infer.

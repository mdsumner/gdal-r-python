# C++ Strings for R/GDAL Users

A practical guide to the string types you encounter when writing R packages that wrap GDAL, and how to convert between them without losing your mind.

## Why this is hard

You are working at the intersection of four different memory management philosophies, each with its own idea of who owns a string, whether it can be modified, and how long a pointer to it remains valid.

- **C**: a string is an address. Ownership is only via loose agreement.
- **C++ standard library**: a string is a value type. Copy by default, RAII cleanup.
- **R's C API**: strings are garbage-collected, interned, immutable objects. The GC can move things.
- **GDAL**: strings are C strings, but with its own allocation conventions (CSL lists, internal buffers, caller-owns vs callee-owns).

The confusion isn't in any one system — it's in crossing the boundaries between them. Every crossing is a potential copy, a potential dangling pointer, or a potential leak.

## The six types you actually encounter

### 1. `const char*` — raw C string

Just a pointer to a null-terminated byte sequence. No ownership semantics whatsoever. When GDAL returns one, you must know from the documentation (or the source) whether:

- It points to an internal buffer that may be invalidated by the next API call (common with `GetDescription()`, `GetProjectionRef()` etc.)
- It points to memory you now own and must `CPLFree()` (less common, but it happens)

There is no type-level distinction between these cases. This is the fundamental trap.

```cpp
// This is fine — immediate use, no storage
const char* desc = GDALGetDescription(hBand);
Rprintf("Band: %s\n", desc);

// This is dangerous — pointer may not survive
const char* proj = GDALGetProjectionRef(hDS);
// ... lots of other GDAL calls ...
use(proj);  // proj may now point to garbage
```

**Rule of thumb**: if you need a C string to survive beyond the immediate statement, copy it into a `std::string`.

### 2. `std::string` — C++ owned string

A value type that owns its memory. Copies on assignment. Automatically freed when it goes out of scope. The safe "meeting point" between all the other string worlds.

Key methods:
- `.c_str()` — returns a `const char*` that is valid only while the `std::string` is alive and unmodified
- Construction from `const char*` — copies the bytes immediately

```cpp
// Safe: immediate copy from GDAL internal pointer
std::string proj(GDALGetProjectionRef(hDS));
// proj now owns its own copy, safe to use whenever

// Dangerous: dangling pointer
const char* p = std::string("hello").c_str();
// the temporary std::string is already dead, p is garbage
```

The `.c_str()` lifetime rule is the single most common source of bugs when passing C++ strings to C APIs. If the `std::string` is a temporary or a local about to go out of scope, the pointer dies with it.

### 3. `char**` / CSL — GDAL string lists

GDAL's `CSLConstList` (`char**`) is a null-terminated array of null-terminated strings — essentially a hand-rolled `std::vector<std::string>` from the C era. You encounter these constantly: open options, creation options, metadata.

The typedef `CSLConstList` (`const char* const*`) is GDAL's way of saying "read-only string list" — you can't modify the array or the strings in it. GDAL has been progressively migrating function signatures from `char**` to `CSLConstList` since GDAL 3.x: `ICreateLayer`, `SetIgnoredFields`, `BuildOverviews`, driver `Create`/`CreateCopy`, and many more now take `CSLConstList`. This is part of a wider const-correctness push (see [GDAL #4459](https://github.com/OSGeo/gdal/issues/4459)) that makes ownership semantics clearer at the type level — if a function takes `CSLConstList`, it won't modify or free your list. If it still takes `char**`, it might.

Key functions:
- `CSLAddString(char** list, const char* str)` — appends a copy, may reallocate. **Returns the new list pointer** (you must use the return value, not the old pointer).
- `CSLSetNameValue(char** list, const char* name, const char* value)` — for KEY=VALUE style options.
- `CSLCount(CSLConstList list)` — number of entries.
- `CSLFetchNameValue(CSLConstList list, const char* name)` — returns internal pointer (don't free).
- `CSLDestroy(char** list)` — frees everything. Call this when you own the list.

```cpp
// Building options for GDALWarp
char** opts = nullptr;  // always start with nullptr, not uninitialized
opts = CSLAddString(opts, "-t_srs");
opts = CSLAddString(opts, "EPSG:4326");
opts = CSLAddString(opts, "-r");
opts = CSLAddString(opts, "bilinear");

GDALWarp(/* ... */, opts);
CSLDestroy(opts);  // you built it, you destroy it
```

**The ownership trap**: some GDAL functions return `char**` you must destroy, others return internal pointers you must not destroy. The function signature is identical in both cases:

```cpp
// GDALGetMetadata — returns INTERNAL pointer, do NOT CSLDestroy
char** md = GDALGetMetadata(hDS, nullptr);
// just read from md, never free it

// GDALGetMetadataDomainList — returns CALLER-OWNED list, you MUST CSLDestroy
char** domains = GDALGetMetadataDomainList(hDS);
// ... use domains ...
CSLDestroy(domains);
```

The documentation is your only guide here. When in doubt, check the GDAL source.

### 4. `STRSXP` / `CHARSXP` — R's C-level strings

R's string model is unlike anything in C or C++:

- Individual strings are `CHARSXP` — immutable, interned in a global cache. If `"hello"` appears in 50 different character vectors, there is one `CHARSXP` object.
- String vectors are `STRSXP` — a vector of pointers to `CHARSXP` objects.
- Everything is garbage collected. You must `PROTECT()` any `SEXP` you create while allocating other objects.
- `NA_STRING` is a real sentinel value (a specific `CHARSXP` pointer), not just a convention.
- Each `CHARSXP` carries an encoding mark (UTF-8, Latin-1, native, or bytes).

```c
// Creating a string vector of length 3 in R's C API
SEXP out = PROTECT(Rf_allocVector(STRSXP, 3));
SET_STRING_ELT(out, 0, Rf_mkCharCE("hello", CE_UTF8));
SET_STRING_ELT(out, 1, Rf_mkCharCE("world", CE_UTF8));
SET_STRING_ELT(out, 2, NA_STRING);
UNPROTECT(1);
return out;
```

To read strings out:
```c
// CHAR() gives you a const char* to the internal CHARSXP bytes
const char* s = CHAR(STRING_ELT(my_strsxp, 0));
// This pointer is valid as long as the CHARSXP exists (which is
// effectively forever, because interned strings are rarely collected).
// But don't rely on this in complex code — copy if in doubt.
```

`Rf_translateCharUTF8()` is important: it re-encodes to UTF-8 if the element is in a different encoding. Always use this when passing to GDAL, which generally expects UTF-8.

### 5. `Rcpp::CharacterVector`

Rcpp wraps `STRSXP` with C++ convenience. The key surprise is that indexing returns **a proxy object**, not a `std::string`:

```cpp
Rcpp::CharacterVector cv = {"hello", "world"};

// This is a proxy, not a string
auto x = cv[0];  // type is Rcpp::CharacterVector::Proxy

// To get a std::string, be explicit:
std::string s = Rcpp::as<std::string>(cv[0]);

// Or use the String type
Rcpp::String rs = cv[0];
```

Implicit conversions exist and mostly do what you want, but they copy. Building up character vectors element-by-element is straightforward:

```cpp
Rcpp::CharacterVector result(n);
for (int i = 0; i < n; i++) {
    result[i] = some_std_string;  // implicit conversion, copies
}
```

### 6. `cpp11::strings` / `cpp11::writable::strings`

cpp11 makes the read-only vs writable distinction explicit at the type level, which is a significant improvement over Rcpp for clarity:

- `cpp11::strings` — read-only view of an R character vector. Cannot modify elements.
- `cpp11::writable::strings` — a writable character vector.
- `cpp11::r_string` — a single string element.

```cpp
[[cpp11::register]]
cpp11::writable::strings get_metadata(cpp11::strings dsn) {
    // dsn is read-only, fine for passing to GDAL
    const char* path = CHAR(STRING_ELT(dsn, 0));  // can drop to R API
    
    // Or using cpp11's own accessors:
    std::string path_str(dsn[0]);  // copies
    
    cpp11::writable::strings out;
    out.push_back("key=value");  // builds up result
    return out;
}
```

cpp11 also has `cpp11::as_cpp<std::string>()` and `cpp11::as_sexp()` for explicit conversions.


## The conversion matrix

This is the practical core. "I have X, I need Y — what do I call, and does it copy?"

| From ↓ / To → | `const char*` | `std::string` | CSL `char**` | `STRSXP` | `Rcpp::CharacterVector` | `cpp11::writable::strings` |
|---|---|---|---|---|---|---|
| **`const char*`** | — | `std::string s(p)` **copy** | `CSLAddString(list, p)` **copy** | `Rf_mkChar(p)` + `SET_STRING_ELT` **copy+intern** | `cv.push_back(p)` **copy** | `out.push_back(p)` **copy** |
| **`std::string`** | `s.c_str()` **view!** | — | `CSLAddString(list, s.c_str())` **copy** | `Rf_mkChar(s.c_str())` + `SET_STRING_ELT` **copy+intern** | `cv.push_back(s)` **copy** | `out.push_back(s)` **copy** |
| **CSL `char**`** | `list[i]` **view!** | `std::string(list[i])` **copy** | — | loop + `SET_STRING_ELT` **copy** | loop + `push_back` **copy** | loop + `push_back` **copy** |
| **`STRSXP`** | `CHAR(STRING_ELT(x,i))` **view** | `std::string(CHAR(...))` **copy** | loop + `CSLAddString` **copy** | — | `Rcpp::CharacterVector(x)` **wrap** | `cpp11::strings(x)` **wrap** |
| **`Rcpp::CharacterVector`** | `CHAR(STRING_ELT(cv,i))` **view** | `Rcpp::as<std::string>(cv[i])` **copy** | loop + `CSLAddString` **copy** | implicit (it *is* a STRSXP) | — | n/a (don't mix) |
| **`cpp11::strings`** | `CHAR(STRING_ELT(x,i))` **view** | `std::string(x[i])` **copy** | loop + `CSLAddString` **copy** | implicit (it *is* a STRSXP) | n/a (don't mix) | `.data()` or construct |

**Key observations**:

- Almost everything **copies**. The only "free" conversions are views via `.c_str()` or `CHAR()`, both of which have lifetime constraints.
- Going *into* R always copies and interns. Going *out of* R via `CHAR()` is a view into R's string pool.
- `std::vector<std::string>` (not shown) is the safest intermediate when shuffling many strings between systems. It owns everything, has clear lifetime, and converts to/from all the others straightforwardly.


## Common patterns in GDAL wrappers

### Pattern 1: Reading GDAL metadata into R

GDAL returns a `char**` you don't own. Convert to R immediately.

```cpp
// Using cpp11
[[cpp11::register]]
cpp11::writable::strings gdal_get_metadata(SEXP ds_xptr, cpp11::strings domain) {
    GDALDatasetH hDS = /* unwrap external pointer */;
    const char* dom = (domain.size() > 0) ? CHAR(STRING_ELT(domain, 0)) : nullptr;
    
    // Internal pointer — do NOT CSLDestroy
    char** md = GDALGetMetadata(hDS, dom);
    
    cpp11::writable::strings out;
    if (md != nullptr) {
        int n = CSLCount(md);
        for (int i = 0; i < n; i++) {
            out.push_back(md[i]);  // copies each string into R's pool
        }
    }
    return out;
}
```

### Pattern 2: Passing R character vector as GDAL open options

Build a CSL from R input, use it, destroy it.

```cpp
[[cpp11::register]]
SEXP open_dataset(cpp11::strings dsn, cpp11::strings open_options) {
    const char* path = CHAR(STRING_ELT(dsn, 0));
    
    // Build CSL from R character vector
    char** oo = nullptr;
    for (int i = 0; i < open_options.size(); i++) {
        oo = CSLAddString(oo, CHAR(STRING_ELT(open_options, i)));
    }
    
    GDALDatasetH hDS = GDALOpenEx(path, GDAL_OF_READONLY, nullptr, oo, nullptr);
    CSLDestroy(oo);  // always clean up, even if GDALOpenEx failed
    
    // ... wrap hDS as external pointer and return ...
}
```

### Pattern 3: The `std::vector<std::string>` staging area

When you need to build up strings from multiple GDAL calls before returning to R, `std::vector<std::string>` is the safe middle ground.

```cpp
[[cpp11::register]]
cpp11::writable::strings get_all_band_descriptions(SEXP ds_xptr) {
    GDALDatasetH hDS = /* unwrap */;
    int nbands = GDALGetRasterCount(hDS);
    
    // Stage in C++ owned storage
    std::vector<std::string> descs;
    descs.reserve(nbands);
    for (int i = 1; i <= nbands; i++) {
        GDALRasterBandH hBand = GDALGetRasterBand(hDS, i);
        // GetDescription returns internal pointer — 
        // std::string constructor copies immediately
        descs.emplace_back(GDALGetDescription(hBand));
    }
    
    // Now convert to R in one go
    cpp11::writable::strings out(nbands);
    for (int i = 0; i < nbands; i++) {
        out[i] = descs[i];
    }
    return out;
}
```

### Pattern 4: Config option RAII guard

Setting and restoring GDAL config options safely:

```cpp
class CPLConfigGuard {
    std::string key_;
    std::string old_value_;
    bool had_old_;
public:
    CPLConfigGuard(const char* key, const char* value) : key_(key) {
        const char* old = CPLGetConfigOption(key, nullptr);
        had_old_ = (old != nullptr);
        if (had_old_) old_value_ = old;  // copy before setting
        CPLSetConfigOption(key, value);
    }
    ~CPLConfigGuard() {
        CPLSetConfigOption(key_.c_str(),
                           had_old_ ? old_value_.c_str() : nullptr);
    }
};

// Usage:
{
    CPLConfigGuard guard("GDAL_HTTP_TIMEOUT", "30");
    // ... do GDAL I/O ...
}  // config restored automatically
```

### Pattern 5: CLI-style argument lists (the tokenization trap)

Many GDAL utility wrappers (GDALWarp, GDALTranslate, GDALBuildVRT, etc.) accept arguments as string lists — the same `char**` format. But there's a critical rule: **each token is a separate element**. This trips everyone up at least once.

```cpp
// WRONG: one string with spaces — GDAL won't parse this
char** args = nullptr;
args = CSLAddString(args, "-ts 1024 0");      // BROKEN
args = CSLAddString(args, "-co COMPRESS=LZ4"); // BROKEN

// RIGHT: each token is its own element
char** args = nullptr;
args = CSLAddString(args, "-ts");
args = CSLAddString(args, "1024");
args = CSLAddString(args, "0");
args = CSLAddString(args, "-co");
args = CSLAddString(args, "COMPRESS=LZ4");
```

This matters because when you're building these from R, the natural R interface is a character vector:

```r
# In R, this is the correct form:
c("-ts", "1024", "0", "-co", "COMPRESS=LZ4")

# NOT this (common mistake):
c("-ts 1024 0", "-co COMPRESS=LZ4")
```

The confusion comes from thinking of these as "command lines" — they look like `gdalwarp -ts 1024 0`, so you might expect one string that gets split. But they're `argv`-style arrays: pre-tokenized, no shell parsing. GDAL's `GDALTranslateOptionsNew`, `GDALWarpAppOptionsNew` etc. take `char**` and walk through it element by element.

A helper for converting R character vectors to GDAL argv-style lists:

```cpp
// Build argv from R character vector, use it, clean up
char** r_to_csl(cpp11::strings x) {
    char** out = nullptr;
    for (int i = 0; i < x.size(); i++) {
        out = CSLAddString(out, CHAR(STRING_ELT(x, i)));
    }
    return out;
}
// Caller must CSLDestroy the result
```

The inverse is also worth noting: if you have a single string that *is* a command line (e.g. from a user text input), GDAL provides `CSLTokenizeString2()` to split it into a `char**`, handling quoting and escaping. But in the R-to-GDAL path, you almost never need this because R already gives you tokenized vectors.

### Trap 1: Dangling `.c_str()`

```cpp
// BUG: temporary std::string dies at the semicolon
const char* bad = std::string("EPSG:4326").c_str();
GDALSetProjection(hDS, bad);  // undefined behaviour

// FIX: keep the std::string alive
std::string srs = "EPSG:4326";
GDALSetProjection(hDS, srs.c_str());  // fine

// ALSO FINE: string literal has static lifetime
GDALSetProjection(hDS, "EPSG:4326");  // fine, literals live forever
```

### Trap 2: Holding GDAL internal pointers too long

```cpp
// BUG: GetProjectionRef returns pointer to internal buffer
const char* proj = GDALGetProjectionRef(hDS);
GDALClose(hDS);  // buffer freed
use(proj);  // undefined behaviour

// FIX: copy immediately
std::string proj(GDALGetProjectionRef(hDS));
GDALClose(hDS);
use(proj.c_str());  // fine
```

### Trap 3: Forgetting CSLDestroy

```cpp
// LEAK: you created it, you must destroy it
char** opts = nullptr;
opts = CSLAddString(opts, "-of");
opts = CSLAddString(opts, "GTiff");
GDALTranslate(/* ... */, opts);
// opts is leaked

// FIX:
CSLDestroy(opts);

// BETTER: use a RAII wrapper or scope guard
```

### Trap 4: Destroying a CSL you don't own

```cpp
// BUG: GDALGetMetadata returns internal storage
char** md = GDALGetMetadata(hDS, nullptr);
CSLDestroy(md);  // corrupts GDAL's internal state

// FIX: just read it, don't destroy it
char** md = GDALGetMetadata(hDS, nullptr);
// read what you need, then forget about it
```

### Trap 5: R string encoding

```cpp
// RISKY: CHAR() returns bytes in the element's encoding, which might
// not be UTF-8 on Windows with non-ASCII paths
const char* path = CHAR(STRING_ELT(dsn, 0));

// SAFER: re-encode to UTF-8
const char* path = Rf_translateCharUTF8(STRING_ELT(dsn, 0));
```


## The mental model

If you remember nothing else, remember this:

1. **`std::string` is the safe harbour**. When crossing any boundary, copy into a `std::string` first. Yes, it copies. That's the point — it gives you ownership and a clear lifetime.

2. **Every `const char*` is a question**: who owns this memory, and when does it die? Answer that question before you use it.

3. **Going into R always copies and interns**. There is no way to hand R a pointer to your buffer. Accept this.

4. **CSL lists follow malloc/free rules**. If you built it (with `CSLAddString`, `CSLSetNameValue`, etc.), you destroy it. If GDAL returned it from an accessor, you don't.

5. **`std::vector<std::string>` is the best staging container** for building up results from multiple GDAL calls before bulk-converting to R. It's the type where C++ and R's philosophies most agree: everything is copied, everything is owned, everything is cleaned up automatically.


## Python: the options representation maze

The C/C++ story is about memory and lifetime. Python doesn't have those problems (GC handles it), but it has a different confusion: there are multiple representations for the same underlying `char**`, and they don't always compose or interchange cleanly.

### The five ways to pass options

**1. String lists (C-style, leaked through)**

The most direct mapping to GDAL's C API. Still KEY=VALUE strings:

```python
ds = gdal.Open("file.tif")
gdal.Translate("out.tif", ds, creationOptions=["COMPRESS=LZ4", "TILED=YES"])
```

This is what all the others eventually become.

**2. Keyword arguments (Pythonic wrappers)**

The `gdal.Translate`, `gdal.Warp`, etc. utility functions accept Python keyword arguments:

```python
gdal.Translate("out.tif", ds, width=1024, height=0, format="GTiff")
```

Looks clean. But these get serialized to argv-style string lists internally — `width=1024` becomes `["-width", "1024"]`. Not all C-level options have keyword equivalents, and the mapping isn't always obvious from the Python side.

**3. Options objects**

```python
opts = gdal.TranslateOptions(width=1024, height=0, creationOptions=["COMPRESS=LZ4"])
gdal.Translate("out.tif", ds, options=opts)
```

These wrap the same argv construction in a named object. The trap: you can pass *either* an options object *or* keyword arguments to the utility function, but mixing them is unreliable.

**4. Dicts (sometimes)**

Some contexts accept Python dicts that get flattened:

```python
# Some wrappers accept this
ds = gdal.OpenEx("file.tif", open_options={"NUM_THREADS": "ALL_CPUS"})
# which becomes ["NUM_THREADS=ALL_CPUS"] internally
```

But this isn't universal — not all functions that take string lists accept dicts.

**5. JSON strings**

Newer APIs, especially for multidimensional data, accept JSON:

```python
# Some creation options or driver-specific configs use JSON
gdal.MultiDimTranslate("out.zarr", ds, 
    creationOptions=["ARRAY:COMPRESS=zstd"])
```

And some driver metadata is returned as JSON strings that you then need to `json.loads()`.

### The underlying issue

All of these are skins over GDAL's C-level `char**`. The Python bindings have evolved over many years, adding progressively more Pythonic interfaces without removing the older ones. So you end up needing to know:

- Which representation a given function actually accepts (string list? kwargs? options object? all three?)
- Whether creation options vs open options vs config options vs CLI-style args are expected (they look similar but go to different places)
- That `-co COMPRESS=LZ4` on the command line becomes `creationOptions=["COMPRESS=LZ4"]` in Python (not `co=["COMPRESS=LZ4"]`)

The tokenization trap from C++ exists here too: `"-ts 1024 0"` as a single string won't work; it needs to be `["-ts", "1024", "0"]` if you're passing raw argv-style args.

### The practical advice

When in doubt, use the string list form. It maps 1:1 to what the C API expects, it's what the GDAL docs describe, and it works everywhere. The Pythonic keyword wrappers are nice when they work, but they're a convenience layer with gaps — and when something goes wrong, debugging is easier if you can see the exact strings being passed.




| Task | Function | Notes |
|---|---|---|
| R char element → C string | `CHAR(STRING_ELT(x, i))` | View, valid while CHARSXP lives |
| R char element → UTF-8 C string | `Rf_translateCharUTF8(STRING_ELT(x, i))` | May copy if re-encoding needed |
| C string → R char element | `SET_STRING_ELT(x, i, Rf_mkCharCE(s, CE_UTF8))` | Copies and interns |
| C string → std::string | `std::string s(p)` | Copies |
| std::string → C string | `s.c_str()` | View! Lifetime of s |
| Build CSL | `list = CSLAddString(list, s)` | Copies, reassigns list pointer |
| CSL → count | `CSLCount(list)` | |
| CSL → element | `list[i]` or `CSLGetField(list, i)` | View |
| Destroy CSL | `CSLDestroy(list)` | Only if you own it |
| Check NA | `STRING_ELT(x, i) == NA_STRING` | Pointer comparison |
| Set NA | `SET_STRING_ELT(x, i, NA_STRING)` | |

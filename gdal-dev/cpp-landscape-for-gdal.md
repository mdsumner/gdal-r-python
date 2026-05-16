# A small landscape map for reading and writing GDAL C++

Three things tangle together when you read GDAL code: the C-API-vs-C++ split,
when to use `auto`, and when to take a reference. They look like one big
"C++ syntax" blob. They're three independent decisions. Once they're prised
apart, each one is small.

---

## 1. Handles vs objects (the C-API marker)

A function name like `GDALGetDataTypeName(...)` doesn't tell you which API
you're in. The **type of the first argument** does.

- **Handle** — `GDALDatasetH`, `GDALMDArrayH`, `GDALGroupH`, `GDALExtendedDataTypeH`
  (capital `H` suffix). Opaque pointer to incomplete type. You **cannot**
  write `.method()` on it — the compiler doesn't know the layout. Forced
  into `GDALSomethingDoThing(h, ...)` style. That's the C API.

- **Object** — `GDALExtendedDataType`, `GDALMDArray`, `GDALGroup`, etc.,
  typically held by value or via `std::shared_ptr<>`. You call methods
  directly: `dt.GetNumericDataType()`, `poArray->GetDimensions()`. That's C++.

**Rule:** handles are the marker of the C API, not function names.
`GDALGetDataTypeName(enum_value)` is a C-linkage helper that happens to work
on a plain enum — calling it from C++ is fine and idiomatic, not
code-switching. You'll see this everywhere. The line

```cpp
const char* name = GDALGetDataTypeName(dt.GetNumericDataType());
```

has both worlds in one expression and that's normal.

---

## 2. `auto` — hide noise, don't hide meaning

`auto` is a tool, not a default.

- **Use `auto`** when the type is long, templated, or obvious from the
  right-hand side. Smart pointers, vectors of smart pointers, iterator types.
  ```cpp
  auto poRootGroup = poSrcDS->GetRootGroup();   // shared_ptr<GDALGroup>
  const auto& apoDims = poArray->GetDimensions(); // vector<shared_ptr<...>>
  ```

- **Spell out the type** when it's short, scalar, or genuinely informative —
  especially enums and integer-width-mattering types.
  ```cpp
  GDALDataType nDataType = dt.GetNumericDataType();  // enum — say so
  size_t nDims = apoDims.size();
  ```

The principle: `auto` removes noise. If the type is *itself* the information
(an enum value, a specific integer width), removing it makes the code worse
to read.

---

## 3. `&` — copy or borrow

`auto` and `&` are **independent decisions**.

- `auto` answers: do I spell out the type, or let the compiler deduce it?
- `&` answers: do I take a copy, or borrow a reference?

Mix freely: `auto`, `auto&`, `const auto&`, `auto*`, `const auto*`.

**Working heuristic:**

- Small/scalar returns (int, enum, pointer, `bool`) → no `&`, copying is free.
- Object returns (vectors, strings, `GDALExtendedDataType`, anything with a
  destructor or heap state) → `const auto&` when you only need to read.
- About to mutate or move from it → drop the `const`.

```cpp
auto nDataType = dt.GetNumericDataType();    // enum, copy is free
const auto& apoDims = poArray->GetDimensions();  // vector — borrow it
const auto& dt = poArray->GetDataType();     // object — borrow it
```

A `const auto& x = expr;` and `const auto& x(expr);` are equivalent for
everyday use — pick one and move on. Modern code leans toward `=`.

---

## 4. GDAL prefix conventions — what survives `auto`

When you write `auto`, the type name disappears from the line. The GDAL house
prefix system carries the lost information for the reader:

| prefix | meaning             | example                              |
|--------|---------------------|--------------------------------------|
| `po`   | pointer-to-object   | `poSrcDS`, `poRootGroup`, `poArray`  |
| `apo`  | array of `po`       | `apoDims`                            |
| `an`   | array of numbers    | `anBlockSize`                        |
| `n`    | integer scalar      | `nDataType`, `nDims`                 |
| `osz`  | string (`std::string`) | `oszName`                         |
| `m_`   | class member        | `m_array`, `m_inputDataset`          |

`auto poFoo` and `const auto& anBar` work *because* the prefix already told
the reader what kind of thing it is. The naming is the type signal once
`auto` strips the spelled-out type away.

---

## Two-rule summary that gets you 95% of the way

1. Reach for `const auto&` for anything object-shaped; plain `auto` for
   pointers and small types.
2. Spell out the type for scalars where the type carries meaning (enums,
   sized integers).

Stop sweating fluency. The combinations come from exposure.

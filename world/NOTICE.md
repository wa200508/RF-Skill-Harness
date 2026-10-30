# Licensing and upstream source

GNU Radio World is copyright © 2026 Marc Lichtman and licensed
**GPL-3.0-or-later**. This directory's editor extension is distributed under the
same license; [COPYING](COPYING) contains the license. The independent Python
harness/backend remains Apache-2.0. Upstream and its dependencies retain their
own copyright and license notices. Packaging them together does not relicense
GNU Radio World as Apache-2.0.

The exact upstream revision and every downloaded runtime/Pyodide artifact hash
are recorded in [upstream-lock.json](upstream-lock.json). The source for this
revision, build scripts, dependency pins and pristine submodule references are at:

https://github.com/777arc/gnuradio-world/tree/96e805fb694e3e797b72ebfad30232c732e3f257

Our modifications are `rf-extension.ts`, `rf-extension.css` and the explicit
checked source transformations in `../scripts/build_world.py`. The image serves
the modified editor source at `/corresponding-source/editor/src/` and the build
adapter at `/corresponding-source/build_world.py`. This repository supplies the
extension, lock and Docker build recipe. Upstream's README and `docs/building.md`
explain the complete Emscripten/Qt/GNU Radio build; `git clone --recurse-submodules`
and checkout of the locked revision retrieves its pinned upstream modules.

The prebuilt DSP artifacts are obtained directly from the upstream project's
public site at build time and verified before use. They are not GNU Radio
packages authored by this harness. NumPy, SciPy and Pyodide are likewise pinned
upstream builds. The image includes GNU Radio World's license and preserves
wheel/source notices; consult those projects for their corresponding licenses.

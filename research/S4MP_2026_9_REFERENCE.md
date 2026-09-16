# S4MP 2026.9 reference reconnaissance

Scope: static, clean-room inspection of the supplied launcher and extracted PE
resources. The executable was not run, patched, or modified.

## Findings

| Claim | Classification | Evidence |
|---|---|---|
| The artifact is a Windows PE executable with native code sections. | CONFIRMED | `s4mp-online-launcher-2026.9.0.exe`, 19,623,856 bytes; PE section names `.text`, `.rdata`, `.data`, `.pdata`, `.rsrc`, `.reloc`; extracted section files supplied under `s4mp-extract`. |
| The resource tree contains version, manifest, and icon resources. | CONFIRMED | `.rsrc/version.txt`, `.rsrc/MANIFEST/1`, `.rsrc/GROUP_ICON/32512`, and `.rsrc/ICON/*.ico`. |
| The quick readable-string scan does not expose embedded Python source, `.pyc`, protobuf descriptors, or obvious travel/BuildBuy/wall/network module names. | CONFIRMED | ASCII-oriented scan of the supplied executable returned PE boilerplate/native symbols and no requested domain string cluster. This is absence of evidence, not proof that those concepts are absent. |
| The launcher likely contains or loads a native launcher/runtime boundary. | STRONG INFERENCE | Native PE code sections and the absence of readable Python/module payload names are consistent with a compiled launcher, but imports/load behavior was not available in the supplied dumps. |
| A travel barrier, wall operation format, native bridge, or event ordering can be reconstructed from these artifacts. | BLOCKED | No behavioral trace, symbols, readable module names, or executable test was available in the supplied reference material. |

## Clean-room implementation consequence

KerMP keeps a localhost-only game bridge and an independent host-authoritative
LAN protocol. Build operations are explicit data (`wall.create` and
`wall.delete`) and travel is an epoch barrier. These are independent design
choices, not copied implementation code. Authentication, entitlement, and
account behavior were intentionally excluded.


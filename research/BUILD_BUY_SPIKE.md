# Build/Buy spike

**UNCERTAIN**

The supplied 2026.9 launcher cannot establish where wall drag or sledgehammer
operations are captured or whether remote application requires Build/Buy UI
state. Static section/resource inspection exposed only the native PE structure,
version/manifest/icons, and no readable BuildBuy/wall/native-bridge module
names. No runtime tracing was performed.

KerMP is consequently ready around a `BuildBackend` contract, but
`SimsNativeBuildBackend` reports unavailable until a legitimate Sims-side hook
is identified and tested in-game. The next useful experiment is a controlled
Sims-side wall create/delete capture that records level, endpoints, wall id,
and the active Build/Buy context, then applies the same operation on a second
machine through the localhost bridge.


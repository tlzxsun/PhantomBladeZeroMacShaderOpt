Title: Experimental shader-cache workaround for Phantom Blade Zero on CrossOver

Phantom Blade Zero kept recompiling shaders on my M5 Mac, even after a completed compilation and normal exit.

On my macOS build, I found a 1 GiB cap in Metal's filesystem compiler cache. This experimental hook passes a 2 GiB budget to the underlying cache. The game's live function-cache entries grew to about 1.69 GiB, and subsequent startup felt much faster. Some loading pauses remain; this is not an FPS or universal stutter fix.

[Source and install/restore scripts](https://github.com/tlzxsun/PhantomBladeZeroMacShaderOpt)

Tested with macOS 27.2 (26B123), CrossOver 26.3, and D3DMetal. It uses private APIs and locally re-signs CrossOver's shared Wine launchers, so please read the limitations and recovery instructions first. No vendor binaries are included.

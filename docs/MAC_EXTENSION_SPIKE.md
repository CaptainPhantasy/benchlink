# Mac Grok.app extension-surface spike (2026-11-05)

**Device:** Mac-mini.local via Desktop Commander (read-only)  
**App:** `/Volumes/Storage/Floyds_Labs_Staff/Grok.app` v0.2.50 `com.grokapp.desktop`

## Verified
1. Bundle `Contents/` has `MacOS/`, `Resources/`, `_CodeSignature/` only — **no `PlugIns/`**, **no `Library/`**.
2. `Info.plist`: document type `.grokskin` only; URL scheme `grok` for skins. No extension point for third-party desktop plugins.
3. User-guide `09-plugins.md` describes **agent marketplace plugins** (skills/hooks/MCP for Grok Build CLI), not a host API to embed Grok Bot staff sessions in the desktop UI.
4. Headless `grok -p` is Grok Build CLI scripting — not a Grok Bot bridge.
5. Prior Ethan probe: Session API `http://127.0.0.1:63301` localhost-only → Grok Build.

## Conclusion (D2)
Desktop extension surface for staff-bot bridging is **sealed**. MVP path = **Mac client stub / sidecar** talking Tailscale HTTP(S) to inference `bridge-daemon`, per frozen D2. Do not hijack Build Session API or treat agent plugins as the bridge.

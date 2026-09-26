# Project delivery rule

After changing the desktop app, rebuild and install the macOS app with
`./build_mac.command` before reporting the change ready for the user to test.
Verify the build succeeded and that `~/Applications/C1 DMP Toolkit.app` opens
with the updated UI. If the rebuild is blocked, say so explicitly; passing
source tests alone does not make the change available in the native app.

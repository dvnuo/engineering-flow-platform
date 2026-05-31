Run `scripts/prepare-runtime-tools.sh` before building the runtime image. The
script discovers every `cmd/<tool>/main.go` in the adjacent, checked-out, or
cloned `engineering-flow-platform-tools` repository and writes Linux amd64
runtime binaries here by default.

Current generated binaries include `jira`, `confluence`, and `browser`. Future
tools added under `cmd/<tool>` are prepared the same way.

The runtime image copies generated binaries into `/usr/local/bin` so agents can
call them through the EFP `bash` built-in from the workspace. The final runtime
image does not install the Go toolchain.

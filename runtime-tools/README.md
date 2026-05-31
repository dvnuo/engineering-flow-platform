Run `scripts/prepare-runtime-tools.sh` before building the runtime image. The
script builds Linux amd64 `jira` and `confluence` binaries from the adjacent or
checked-out `engineering-flow-platform-tools` repository and writes them here.

The runtime image copies those binaries into `/usr/local/bin` and does not
install the Go toolchain.

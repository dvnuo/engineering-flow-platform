Place prebuilt `jira` and `confluence` binaries from the adjacent
`engineering-flow-platform-tools` release process in this directory before
building the runtime image.

The runtime image copies those binaries into `/usr/local/bin` and does not
install the Go toolchain.

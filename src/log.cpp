#include "va.h"
#include "log.h"

// Complete keeps this release hook out of line: EarlySetup passes the current-directory string
// in ECX at 0x4ed66a and calls the shared bare-ret body at 0x5bc690.
// Mac retains it at 0x221ee0, outside kb's code region, between the gzfile
// initializer and a separate initialization block. The logging filename is
// inferred; keeping its body outside kb preserves the ordinary call boundary.
// Dreamcast kb.cpp:648 calls the older CLogFile::InitLogFile() instead.
// The map editor passes its established directory to the same release hook
// from InitInstance (+0x13e); its retained body at 0x4e3ca0 is also a bare ret.
MAC_ADDRESS(0x221ee0, 0x4)
VA_AT(h3maped, 0x004e3ca0, 0x1)
void initLogFile(const char* path)
{
}

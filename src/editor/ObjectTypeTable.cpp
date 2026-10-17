// The map editor's palette table. The shared ObjectType implementation
// owns parsing; the editor owns this persistent instance and startup call.
// Retail 0x4928e5 constructs the table, 0x49291b binds its public reference,
// and InitInstance calls 0x492926 to load objects.txt into that same object.
#include "editor/stdafx.h"

#include "va.h"
#include "editor/ObjectTypeTable.h"

namespace {
VA_COMPGEN(0x004928e5, 0x16, STATIC_CTOR, g_objectTypeTable)
VA_COMPGEN(0x00492911, 0xa, STATIC_DTOR, g_objectTypeTable)
DATA(0x005a2398) TObjectTypeTable g_objectTypeTable;
}

VA_COMPGEN(0x0049291b, 0xb, STATIC_CTOR, kObjectTypeTable)
DATA(0x005a23a8) const TObjectTypeTable& kObjectTypeTable = g_objectTypeTable;

VA(0x00492926, 0x10)
void loadObjectTypeTable()
{
    g_objectTypeTable.load("objects.txt");
}

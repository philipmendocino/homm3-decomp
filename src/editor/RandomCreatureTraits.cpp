// RandomCreatureTraits.cpp - the map editor's random-creature names.
// Retail InitInstance calls 0x458605 after the ordinary creature table;
// this body reads crgenerc.txt and fills fourteen {name, creatureType} rows.
// The source filename and initializer name are inferred; the data retains the
// signed random-creature IDs -2 through -15 in that order.
#include "editor/stdafx.h"

#include <string.h>

#include "va.h"
#include "autoarrayptr.h"
#include "resourcemanager.h"
#include "resourceptr.h"
#include "textresource.h"
#include "editor/Army.h"
#include "editor/MapEditorText.h"

namespace {
DATA(0x0058b100) TRandomCreatureTraits g_randomCreatureTraits[kNumRandomCreatureTypes] = {
    {NULL, static_cast<TCreatureType>(-2)},
    {NULL, static_cast<TCreatureType>(-3)},
    {NULL, static_cast<TCreatureType>(-4)},
    {NULL, static_cast<TCreatureType>(-5)},
    {NULL, static_cast<TCreatureType>(-6)},
    {NULL, static_cast<TCreatureType>(-7)},
    {NULL, static_cast<TCreatureType>(-8)},
    {NULL, static_cast<TCreatureType>(-9)},
    {NULL, static_cast<TCreatureType>(-10)},
    {NULL, static_cast<TCreatureType>(-11)},
    {NULL, static_cast<TCreatureType>(-12)},
    {NULL, static_cast<TCreatureType>(-13)},
    {NULL, static_cast<TCreatureType>(-14)},
    {NULL, static_cast<TCreatureType>(-15)}
};
}

DATA(0x0058b170) const TRandomCreatureTraits* akRandomCreatureTraits = g_randomCreatureTraits;

VA(0x00458605, 0x11e)
void InitializeRandomCreatureTraitsTable()
{
    TResourcePtr<TTextResource> textResource(ResourceManager::GetText("crgenerc.txt"));
    if (!textResource.get())
        throw TRuntimeError();
    int size = 0;
    unsigned int i;
    for (i = 0; i < kNumRandomCreatureTypes; ++i)
        size += strlen(textResource->GetText(i)) + 1;
    DATA_COMPGEN_GUARD(0x0059fc58, randomCreatureNamesGuard, names)
    VA_COMPGEN(0x00458723, 0x16, STATIC_DTOR, names)
    DATA(0x0059fc50) static TAutoArrayPtr<char> names(new char[size]);
    if (!names.get())
        throw TAllocationFailure();
    char* p = names.get();
    for (i = 0; i < kNumRandomCreatureTypes; ++i) {
        const char* text = textResource->GetText(i);
        int length = strlen(text) + 1;
        memcpy(p, text, length);
        g_randomCreatureTraits[i].m_name = p;
        p += length;
    }
}

// Resource archive selection and disk-header layouts shared by the resource readers.
#ifndef HOMM3_RESOURCEMANAGER_ARCHIVE_H
#define HOMM3_RESOURCEMANAGER_ARCHIVE_H

#include "va.h"

#include "platform.h"

struct SoundHeaderStruct;

// The four state-selected rows at retail 0x69e538 contain three
// (count, LOD-index-list) pairs. The first serves sprites, the second
// bitmaps, and the final pair serves the sound-header archives.
struct TResourceArchiveList {
public:
    int m_count;
    const int* m_indices;
};

// Defined in the class: the map editor's /Ob1 Libraries build still expands
// the constructor into the table's static initializer (h3maped 0x4daec0),
// which only a declared-inline body allows.
struct TResourceArchiveContext {
public:
    TResourceArchiveContext(int spriteCount, const int* sprites,
                            int bitmapCount, const int* bitmaps,
                            int soundCount, const int* sounds)
    {
        m_sprites.m_count = spriteCount;
        m_sprites.m_indices = sprites;
        m_bitmaps.m_count = bitmapCount;
        m_bitmaps.m_indices = bitmaps;
        m_sounds.m_count = soundCount;
        m_sounds.m_indices = sounds;
    }

    TResourceArchiveList m_sprites;
    TResourceArchiveList m_bitmaps;
    TResourceArchiveList m_sounds;
};
SIZE(TResourceArchiveContext, 0x18);

// Dreamcast CodeView's function-local GetBitmap16 record (type 0x289c),
// independently byte-proven by retail's three archive-header reads.
struct TBitmapResourceHeader {
public:
    int m_dataSize;
    int m_width;
    int m_height;
};
SIZE(TBitmapResourceHeader, 0x0c);

struct TResourceLODSlot;
extern TResourceLODSlot g_resourceLodSlots[];
extern TResourceArchiveContext g_resourceArchiveContexts[4];

// Three retail descriptors at 0x69e500. Each points at one header array,
// its count, and the Windows file handle used for the positioned read.
// The constructor is inline like TResourceArchiveContext's: the map
// editor's /O2 Libraries archive expands it into the table initializer
// (h3maped 0x4dae40), which an out-of-line body turns into three calls.
struct TSoundHeaderDescriptor {
public:
    TSoundHeaderDescriptor(SoundHeaderStruct** sounds, int* count, HANDLE* file)
        : m_sounds(sounds), m_count(count), m_file(file)
    {
    }

    SoundHeaderStruct** m_sounds;
    int* m_count;
    HANDLE* m_file;
};
SIZE(TSoundHeaderDescriptor, 0x0c);

#endif

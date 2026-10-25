// TilePoint.h - the neighbours of a map tile (Loki h3maped TilePoint.cpp,
// object 33). The game (tiles.cpp, 0x5bc910) and the map editor (inside
// Tile.cpp's span, 0x4bf3f4) each define this pair; Loki's header file
// name is not proven.
#ifndef HOMM3_TILEPOINT_H
#define HOMM3_TILEPOINT_H

#include "Point.h"

// The eight neighbour directions, clockwise from north. The order is fixed by
// the retail .bss tables (game 0x6a80a8, h3maped 0x5a5028), whose sixteen
// dwords read (0,-1) (1,-1) (1,0) (1,1) (0,1) (-1,1) (-1,0) (-1,-1) - and by
// the mask builder below, which clears exactly the three entries whose dy is
// -1 on the top row, the three whose dy is +1 on the bottom row, the three
// whose dx is -1 in column zero and the three whose dx is +1 in the last
// column. The enumerator names are descriptive; Loki indexes by literal.
enum ETileDirection {
    TILE_DIR_NORTH = 0,
    TILE_DIR_NORTHEAST = 1,
    TILE_DIR_EAST = 2,
    TILE_DIR_SOUTHEAST = 3,
    TILE_DIR_SOUTH = 4,
    TILE_DIR_SOUTHWEST = 5,
    TILE_DIR_WEST = 6,
    TILE_DIR_NORTHWEST = 7,
    TILE_DIR_COUNT = 8
};

// The eight neighbour offsets, clockwise from north (0, -1). Loki preserves
// the name and the const element type.
extern const TPoint<int> akAdjOffset[TILE_DIR_COUNT];

// Clears abDir[i] for each neighbour akAdjOffset[i] that falls off a
// width x height map at (x, y). Each image uses its ordinary calling
// convention: the game builds with /Gr, while the editor's five-argument
// call is cdecl.
void computeAdjacentDirs(unsigned int width, unsigned int height,
                         unsigned int x, unsigned int y,
                         bool (&abDir)[TILE_DIR_COUNT]);

#endif  /* HOMM3_TILEPOINT_H */

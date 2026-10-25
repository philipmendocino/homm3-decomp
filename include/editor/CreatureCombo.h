// Shared creature-combo ordering and insertion for editor dialogs.
#ifndef HOMM3_EDITOR_CREATURECOMBO_H
#define HOMM3_EDITOR_CREATURECOMBO_H

#include "armygrp.h"

class CComboBox;

// The creature lists' order: by town (the neutrals last), then level, then
// id. Loki names it compareCreatureType, file-local to ArmyDlg.cpp (Loki
// ArmyDlg.cpp 33). Complete calls it across translation units and carries
// two byte-identical out-of-line bodies: compareCreatureType (0x40ddf5, in
// EditCreatureStackDlg.cpp), called by the army and creature-stack dialogs,
// and isCreatureBefore (0x4797f2, in MapSpecsVictoryCondPage.cpp), called by
// the victory-condition and reward creature lists. The second body's
// original name is unknown.
bool compareCreatureType(TCreatureType lhs, TCreatureType rhs);

bool isCreatureBefore(TCreatureType lhs, TCreatureType rhs);
void insertCreature(CComboBox& combo, TCreatureType creature);

#endif  /* HOMM3_EDITOR_CREATURECOMBO_H */

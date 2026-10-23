// Shared creature-combo ordering and insertion for reward and victory dialogs.
#ifndef HOMM3_EDITOR_CREATURECOMBO_H
#define HOMM3_EDITOR_CREATURECOMBO_H

#include "armygrp.h"

class CComboBox;

// Retail retains both calls across the two dialog translation units. Keep
// ordinary source bodies at their claimed retail owners.
bool isCreatureBefore(TCreatureType lhs, TCreatureType rhs);
void insertCreature(CComboBox& combo, TCreatureType creature);

#endif  /* HOMM3_EDITOR_CREATURECOMBO_H */

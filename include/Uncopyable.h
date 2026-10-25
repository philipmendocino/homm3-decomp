// Uncopyable.h - a base that forbids copying (Loki h3maped). Its inline
// constructor and RTTI node are linkonce bodies; TTerrainPlacementOp and
// the line filter TMapLineFilter, shared by the game's random-map
// generator and the editors, derive from it. The header name is not
// proven.
#ifndef HOMM3_UNCOPYABLE_H
#define HOMM3_UNCOPYABLE_H

class TUncopyable {
protected:
    TUncopyable() {}

private:
    TUncopyable(const TUncopyable& other);
    TUncopyable& operator=(const TUncopyable& other);
};

#endif  /* HOMM3_UNCOPYABLE_H */

// Shared scalar extraction for both editors. Include after the owning
// edition has defined TRawIStream; the stream class implementations stay separate.
#ifndef HOMM3_EDITOR_RAWSTREAM_VALUE_H
#define HOMM3_EDITOR_RAWSTREAM_VALUE_H

// Return one scalar in the file's width; callers then widen or convert it.
template <class T>
inline T readValue(TRawIStream& stream)
{
    T value;
    stream >> value;
    return value;
}

#endif  /* HOMM3_EDITOR_RAWSTREAM_VALUE_H */

"""Trim compiler fill without cutting x86 instruction operands."""
from functools import lru_cache

#: The census extends a body over the 0xCC fill the linker puts before the
#: next section; that section's alignment bounds it (VC6 code: 16 bytes).
MAX_FILL = 16
#: The smallest alignment a following code section has (ftol.obj: 4).
MIN_FILL_ALIGNMENT = 4


@lru_cache(maxsize=1)
def _decoder():
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    return Cs(CS_ARCH_X86, CS_MODE_32)


def trimmed_end(payload: bytes, start: int, end: int, floor: int = 0) -> int:
    """Return a padding-free endpoint, retaining relocations and instructions.

    A final 90/CC byte may be a jump displacement or an immediate. Decode
    from the symbol start to the apparent fill boundary; if an instruction
    crosses it, retain the whole instruction. Undecodable prefixes provide
    no boundary proof, so leave their extent alone.
    """
    floor = max(start + 1, floor)
    boundary = end
    while boundary > floor and payload[boundary - 1] in (0x90, 0xCC):
        boundary -= 1
    if boundary == end:
        return end
    for address, size, _mnemonic, _operands in _decoder().disasm_lite(
            payload[start:end], start):
        instruction_end = address + size
        if instruction_end >= boundary:
            return max(boundary, instruction_end)
    return end


def is_section_fill(read, hi: int, end: int, alignment: int = MIN_FILL_ALIGNMENT) -> bool:
    """True when [hi, end) is the 0xCC linker fill before an aligned section.

    The fill is shorter than any section alignment and ends where the next
    section starts, on an `alignment` boundary (at least four). `read(lo,
    hi)` returns the image bytes, or None/short bytes outside the section:
    an unreadable span is no fill.
    """
    if not 0 < end - hi < MAX_FILL or end % max(alignment, MIN_FILL_ALIGNMENT):
        return False
    data = read(hi, end)
    return data is not None and len(data) == end - hi and set(data) == {0xCC}

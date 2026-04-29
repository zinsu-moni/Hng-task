import random
import time
import uuid


def uuid7() -> uuid.UUID:
    """
    Generate a UUID version 7 (RFC 9562-like layout).

    - 48 bits: Unix epoch milliseconds
    - 4 bits: version (0b0111)
    - 2 bits: variant (0b10)
    - 74 bits: randomness
    """

    ts_ms = time.time_ns() // 1_000_000
    # 48-bit timestamp
    t = ts_ms & ((1 << 48) - 1)

    time_low = (t >> 16) & 0xFFFFFFFF  # 32 bits
    time_mid = t & 0xFFFF  # 16 bits

    rand_a = random.getrandbits(12)  # 12 bits
    version = 0x7
    time_hi_and_version = ((t >> 12) & 0x0FFF) | (version << 12)  # 16 bits, top 4 bits are version

    # RFC 4122 variant: 0b10xx...
    clock_seq = random.getrandbits(14)  # 14 bits
    clock_seq_hi_variant = 0x80 | ((clock_seq >> 8) & 0x3F)  # 8 bits, top 2 bits are variant
    clock_seq_low = clock_seq & 0xFF  # 8 bits

    node = random.getrandbits(48)  # 48 bits

    fields = (
        time_low,
        time_mid,
        time_hi_and_version,
        clock_seq_hi_variant,
        clock_seq_low,
        node,
    )
    return uuid.UUID(fields=fields)


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

    time_hi = (t >> 16) & 0xFFFFFFFF  # 32 bits
    time_mid = t & 0xFFFF  # 16 bits

    rand_a = random.getrandbits(12)  # 12 bits
    rand_b = random.getrandbits(14)  # 14 bits
    rand_c = random.getrandbits(48)  # 48 bits

    version = 0x7
    time_hi_and_version = (version << 12) | rand_a

    # RFC 4122 variant: 0b10xx...
    clock_seq_and_reserved = 0x8000 | rand_b

    fields = (
        time_hi,
        time_mid,
        time_hi_and_version,
        clock_seq_and_reserved,
        rand_c,
    )
    return uuid.UUID(fields=fields)


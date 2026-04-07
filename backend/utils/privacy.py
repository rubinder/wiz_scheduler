import ipaddress


def mask_ip(ip: str) -> str:
    """Mask the last 2 bytes of an IP address for GDPR compliance.

    IPv4: 192.168.1.42 → 192.168.0.0
    IPv6: 2001:db8::1  → 2001:db8::0:0:0:0:0
    """
    try:
        addr = ipaddress.ip_address(ip)
        if isinstance(addr, ipaddress.IPv4Address):
            masked = ipaddress.IPv4Address(int(addr) & 0xFFFF0000)
        else:
            masked = ipaddress.IPv6Address(int(addr) & (0xFFFFFFFFFFFF << 80))
        return str(masked)
    except ValueError:
        return "0.0.0.0"

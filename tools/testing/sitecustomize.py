"""Synthetic verification processes only: deny non-loopback Python network I/O."""
import ipaddress
import socket
import sys
def _local(host):
    if host == 'localhost': return True
    try: return ipaddress.ip_address(host).is_loopback
    except ValueError: return False
def _audit(event,args):
    if event == 'socket.getaddrinfo' and not _local(args[0]):
        raise PermissionError('Synthetic verification permits loopback DNS only')
    if event in {'socket.connect','socket.bind','socket.sendto'}:
        sock=args[0];address=args[-1]
        if sock.family in {socket.AF_INET,socket.AF_INET6} and (not isinstance(address,tuple) or not _local(address[0])):
            raise PermissionError('Synthetic verification permits loopback sockets only')
sys.addaudithook(_audit)

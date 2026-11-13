"""Request a cooperative repair shutdown through this installation's local IPC."""
import hashlib
import sys
from pathlib import Path


def request_shutdown(root):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket

    app = QCoreApplication.instance() or QCoreApplication([])
    name = "petpal_" + hashlib.sha256(str(Path(root).resolve()).casefold().encode("utf-8")).hexdigest()[:16]
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(1200):
        return "unavailable"
    socket.write(b"setup-exit\n")
    socket.flush()
    if socket.bytesToWrite() and not socket.waitForBytesWritten(1200):
        return "unavailable"
    if not socket.bytesAvailable() and not socket.waitForReadyRead(1800):
        return "unavailable"
    response = bytes(socket.readAll()).strip()
    socket.disconnectFromServer()
    return response.decode("ascii") if response in (b"stopping", b"protected") else "unavailable"


if __name__ == "__main__":
    print(request_shutdown(Path(sys.argv[1])), flush=True)

export class ScenarioEventStream {
  constructor({ onOpen, onEvent, onReconnect, onClose } = {}) {
    this.onOpen = onOpen || (() => {});
    this.onEvent = onEvent || (() => {});
    this.onReconnect = onReconnect || (() => {});
    this.onClose = onClose || (() => {});
    this.socket = null;
    this.scenarioId = null;
    this.reconnectTimer = null;
    this.reconnectAttempt = 0;
    this.generation = 0;
  }

  connect(scenarioId) {
    if (
      this.scenarioId === scenarioId &&
      this.socket &&
      [WebSocket.OPEN, WebSocket.CONNECTING].includes(this.socket.readyState)
    ) {
      return;
    }

    this.disconnect();
    this.scenarioId = scenarioId;
    this.reconnectAttempt = 0;
    this.openSocket();
  }

  openSocket() {
    if (!this.scenarioId) return;
    const generation = ++this.generation;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${location.host}/api/scenarios/${this.scenarioId}/events`;
    const socket = new WebSocket(url);
    this.socket = socket;

    socket.addEventListener("open", () => {
      if (generation !== this.generation) return;
      this.reconnectAttempt = 0;
      this.onOpen();
    });

    socket.addEventListener("message", (message) => {
      if (generation !== this.generation) return;
      try {
        this.onEvent(JSON.parse(message.data));
      } catch (error) {
        this.onEvent({ type: "error", detail: `Evento inválido: ${error.message}` });
      }
    });

    socket.addEventListener("close", () => {
      if (generation !== this.generation || !this.scenarioId) return;
      this.onClose();
      this.scheduleReconnect(generation);
    });

    socket.addEventListener("error", () => {
      socket.close();
    });
  }

  scheduleReconnect(generation) {
    const delay = Math.min(1000 * 2 ** this.reconnectAttempt, 10000);
    this.reconnectAttempt += 1;
    this.onReconnect(delay);
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      if (generation === this.generation && this.scenarioId) this.openSocket();
    }, delay);
  }

  disconnect() {
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.generation += 1;
    const socket = this.socket;
    this.socket = null;
    this.scenarioId = null;
    if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) {
      socket.close(1000, "scenario changed");
    }
  }
}

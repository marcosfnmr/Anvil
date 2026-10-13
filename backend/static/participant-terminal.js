const MAX_OUTPUT_CHARS = 128 * 1024;

export class ParticipantTerminal {
  constructor(dialog) {
    this.dialog = dialog;
    this.status = dialog.querySelector("[data-terminal-status]");
    this.title = dialog.querySelector("#participant-terminal-title");
    this.output = dialog.querySelector("[data-terminal-output]");
    this.form = dialog.querySelector("[data-terminal-form]");
    this.input = dialog.querySelector("[data-terminal-input]");
    this.sendButton = this.form.querySelector('[type="submit"]');
    this.interruptButton = dialog.querySelector("[data-terminal-interrupt]");
    this.socket = null;
    this.decoder = new TextDecoder();
    this.generation = 0;

    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.sendLine();
    });
    this.interruptButton.addEventListener("click", () => this.sendInterrupt());
    dialog.querySelector("[data-terminal-close]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => this.disconnect());
  }

  open(scenarioId, nodeId, deviceName, kind = "participant") {
    this.disconnect();
    const generation = ++this.generation;
    this.output.textContent = "";
    this.title.textContent = kind === "engineering" ? "Terminal de ingeniería" : "Estación de participante";
    this.input.value = "";
    this.setConnected(false);
    this.status.textContent = `Conectando con ${deviceName}…`;
    if (!this.dialog.open) this.dialog.showModal();
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const collection = kind === "engineering" ? "engineering" : "participants";
    const url = `${protocol}//${location.host}/api/scenarios/${encodeURIComponent(scenarioId)}/${collection}/${encodeURIComponent(nodeId)}/terminal`;
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.binaryType = "arraybuffer";
    socket.addEventListener("open", () => {
      if (generation !== this.generation) return;
      this.status.textContent = `Terminal activo · ${deviceName} · límite de una hora.`;
      this.setConnected(true);
      this.input.focus();
    });
    socket.addEventListener("message", (event) => {
      if (generation !== this.generation) return;
      const text = typeof event.data === "string"
        ? event.data
        : this.decoder.decode(new Uint8Array(event.data), { stream: true });
      this.appendOutput(text);
    });
    socket.addEventListener("error", () => {
      if (generation === this.generation) this.status.textContent = "No se pudo abrir el terminal. Comprueba el acceso de la estación y que el escenario esté en ejecución.";
    });
    socket.addEventListener("close", (event) => {
      if (generation !== this.generation) return;
      this.setConnected(false);
      this.status.textContent = event.code === 4409
        ? "Ya existe una sesión abierta para esta estación."
        : event.code === 1013
          ? "Anvil alcanzó el máximo de sesiones de terminal. Inténtalo más tarde."
          : "Terminal desconectado.";
      this.socket = null;
    });
  }

  sendLine() {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    const line = this.input.value;
    if (new TextEncoder().encode(line).length > 2048) {
      this.status.textContent = "El comando supera el límite de 2048 bytes.";
      return;
    }
    this.socket.send(new TextEncoder().encode(`${line}\n`));
    this.input.value = "";
    this.input.focus();
  }

  sendInterrupt() {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(new Uint8Array([3]));
  }

  appendOutput(text) {
    this.output.append(document.createTextNode(text));
    if (this.output.textContent.length > MAX_OUTPUT_CHARS) {
      this.output.textContent = this.output.textContent.slice(-MAX_OUTPUT_CHARS);
    }
    this.output.scrollTop = this.output.scrollHeight;
  }

  setConnected(connected) {
    this.input.disabled = !connected;
    this.sendButton.disabled = !connected;
    this.interruptButton.disabled = !connected;
  }

  disconnect() {
    this.generation += 1;
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) this.socket.close(1000, "Terminal closed by user");
    this.socket = null;
    this.setConnected(false);
  }
}

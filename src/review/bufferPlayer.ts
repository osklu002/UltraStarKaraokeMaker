// Player da tela de revisão sobre a Web Audio API.
//
// POR QUE EXISTE (medido, 29/09/2026, Fedora 44 / WebKitGTK 2.54): no Linux o
// <audio> do WebKitGTK toca o MP3 do pacote, mas o `currentTime` que ele
// informa ERRA de 1 a 2 segundos, de forma irregular (+1,0 s no início, +2,0 s
// depois de pular para 60 s, ~0 depois de pular para 150 s - medido
// comparando o som que sai de verdade, capturado por Web Audio, com o áudio
// decodificado). A revisão desenha as notas contra esse relógio, então o
// chart parecia "nada a ver" com o canto em trechos inteiros, embora o .txt
// estivesse certo (no UltraStar tocava perfeito). O `decodeAudioData` do mesmo
// motor, ao contrário, é exato: difere de uma decodificação por ffmpeg em
// 23 ms constantes (atraso do encoder MP3) em qualquer ponto da música.
//
// Então a revisão toca o PRÓPRIO buffer decodificado (que ela já decodificava
// para a waveform) com AudioBufferSourceNode, e o relógio vem do
// AudioContext - preciso em qualquer plataforma. A interface imita o pedaço
// do HTMLAudioElement que a revisão usa (currentTime, paused, duration,
// play, pause), para o resto do arquivo não precisar mudar - e para o
// <audio> antigo continuar servindo de reserva se a decodificação falhar.

/** O que a tela de revisão usa de um player (HTMLAudioElement também serve). */
export interface ReviewAudio {
  currentTime: number;
  readonly paused: boolean;
  readonly duration: number;
  play(): Promise<void>;
  pause(): void;
}

export class BufferPlayer implements ReviewAudio {
  private buffer: AudioBuffer | null = null;
  private source: AudioBufferSourceNode | null = null;
  /** posição (s) no buffer quando o `source` atual começou (ou onde está pausado) */
  private offset = 0;
  /** ctx.currentTime no instante em que o `source` atual começou */
  private startedAt = 0;
  private _paused = true;

  constructor(
    private readonly ctx: AudioContext,
    private readonly onPlayingChange: (playing: boolean) => void
  ) {}

  /** Troca o áudio (ex.: música completa <-> só voz) mantendo a posição e o estado. */
  setBuffer(buffer: AudioBuffer): void {
    const t = this.currentTime;
    const wasPlaying = !this._paused;
    this.stopSource();
    this.buffer = buffer;
    this.offset = Math.min(Math.max(0, t), buffer.duration);
    if (wasPlaying) this.startSource();
  }

  /** Contexto de áudio do player (a revisão decodifica o arquivo nele). */
  get context(): AudioContext {
    return this.ctx;
  }

  get duration(): number {
    return this.buffer ? this.buffer.duration : NaN;
  }

  get paused(): boolean {
    return this._paused;
  }

  get currentTime(): number {
    if (this._paused || !this.source) return this.offset;
    // o que sai no alto-falante está atrasado pela latência de saída do contexto
    const latency = (this.ctx as AudioContext & { outputLatency?: number }).outputLatency || 0;
    const t = this.offset + Math.max(0, this.ctx.currentTime - this.startedAt - latency);
    return Math.min(t, this.duration);
  }

  set currentTime(t: number) {
    const playing = !this._paused;
    this.stopSource();
    const max = this.buffer ? this.buffer.duration : 0;
    this.offset = Math.min(Math.max(0, t), max);
    if (playing) this.startSource();
  }

  async play(): Promise<void> {
    if (!this.buffer || !this._paused) return;
    if (this.ctx.state === "suspended") await this.ctx.resume();
    if (this.offset >= this.buffer.duration) this.offset = 0;
    this._paused = false;
    this.startSource();
    this.onPlayingChange(true);
  }

  pause(): void {
    if (this._paused) return;
    this.offset = this.currentTime;
    this.stopSource();
    this._paused = true;
    this.onPlayingChange(false);
  }

  /** Libera o contexto de áudio (ao sair da tela). */
  dispose(): void {
    this.stopSource();
    this._paused = true;
    this.ctx.close().catch(() => {});
  }

  private startSource(): void {
    if (!this.buffer) return;
    const src = this.ctx.createBufferSource();
    src.buffer = this.buffer;
    src.connect(this.ctx.destination);
    src.onended = () => {
      // só o fim natural da música (stopSource tira o handler antes de parar)
      if (this.source !== src) return;
      this.source = null;
      this.offset = this.buffer ? this.buffer.duration : 0;
      this._paused = true;
      this.onPlayingChange(false);
    };
    this.startedAt = this.ctx.currentTime;
    src.start(0, this.offset);
    this.source = src;
  }

  private stopSource(): void {
    const src = this.source;
    if (!src) return;
    this.source = null;
    src.onended = null;
    try {
      src.stop();
    } catch {
      // já parado
    }
    src.disconnect();
  }
}

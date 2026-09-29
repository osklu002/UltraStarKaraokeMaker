// Diferenças de plataforma (Windows x Linux/macOS) num lugar só.
//
// Até a v0.21 o main.rs assumia Windows direto: %LOCALAPPDATA%, venv com
// `Scripts\python.exe`, `taskkill` para cancelar e `explorer` para abrir a
// pasta. Cada função aqui devolve EXATAMENTE o que o código antigo fazia no
// Windows (nada muda lá) e o equivalente nativo nos outros sistemas:
//
//   dado              Windows                        Linux / macOS
//   pasta de dados    %LOCALAPPDATA%\USKMaker        $XDG_DATA_HOME/USKMaker ou ~/.local/share/USKMaker
//   python do venv    venv\Scripts\python.exe        venv/bin/python
//   ffmpeg embutido   bin\ffmpeg.exe                 bin/ffmpeg
//   matar a árvore    taskkill /PID <pid> /T /F      kill -KILL -- -<pgid> (grupo próprio, ver abaixo)
//   abrir a pasta     explorer                       xdg-open (Linux) / open (macOS)
//   setup            setup-sidecar.ps1 (PowerShell)  setup-sidecar.sh (bash)

use std::path::{Path, PathBuf};
use std::process::Command;

/// Flag do Windows para criar subprocessos sem janela de console piscando
/// (CREATE_NO_WINDOW) - relevante porque o app roda como GUI.
#[cfg(windows)]
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[cfg(windows)]
use std::os::windows::process::CommandExt as _;

/// Pasta de dados do USKMaker por usuário (venv, ffmpeg embutido, logs,
/// letras aprovadas). `None` = variável de ambiente base ausente.
pub fn data_dir() -> Option<PathBuf> {
    data_dir_from(|k| std::env::var(k).ok())
}

/// Núcleo testável do `data_dir`: recebe o leitor de variáveis de ambiente
/// em vez de ler o processo (testes em paralelo não podem mexer no env real).
fn data_dir_from(get: impl Fn(&str) -> Option<String>) -> Option<PathBuf> {
    let non_empty = |k: &str| get(k).filter(|v| !v.is_empty());
    let base = if cfg!(windows) {
        PathBuf::from(non_empty("LOCALAPPDATA")?)
    } else {
        // XDG Base Directory: só vale caminho absoluto; relativo é ignorado
        // pela especificação e cai no padrão ~/.local/share.
        match non_empty("XDG_DATA_HOME").map(PathBuf::from) {
            Some(p) if p.is_absolute() => p,
            _ => PathBuf::from(non_empty("HOME")?).join(".local").join("share"),
        }
    };
    Some(base.join("USKMaker"))
}

/// Caminho do interpretador dentro de um venv (layouts diferentes por SO).
pub fn venv_python(venv: &Path) -> PathBuf {
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// Nome do executável com a extensão da plataforma (`ffmpeg` -> `ffmpeg.exe`).
pub fn exe_name(stem: &str) -> String {
    if cfg!(windows) {
        format!("{stem}.exe")
    } else {
        stem.to_string()
    }
}

/// Configura o sidecar para poder ser morto com a ÁRVORE inteira depois.
///
/// No Windows o `taskkill /T` já segue a árvore de processos. No Unix não há
/// equivalente por PID: matar só o PID do python deixaria os filhos (Demucs,
/// ffmpeg, yt-dlp) órfãos, segurando CPU/VRAM. Por isso o sidecar nasce num
/// GRUPO de processos próprio (pgid = pid dele) e o cancelamento manda o
/// sinal para o grupo todo - os filhos herdam o grupo.
pub fn detach_process_group(cmd: &mut tokio::process::Command) {
    #[cfg(unix)]
    cmd.process_group(0);
    #[cfg(not(unix))]
    let _ = cmd;
}

/// Comando que mata o sidecar e todos os filhos. No Unix exige que o
/// processo tenha sido criado com `detach_process_group`.
pub fn kill_tree_command(pid: u32) -> Command {
    let (program, args) = kill_tree_args(pid);
    let mut cmd = Command::new(program);
    cmd.args(args);
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

fn kill_tree_args(pid: u32) -> (&'static str, Vec<String>) {
    if cfg!(windows) {
        ("taskkill", vec!["/PID".into(), pid.to_string(), "/T".into(), "/F".into()])
    } else {
        // PID negativo = grupo de processos; o "--" impede o kill de ler o
        // "-<pgid>" como opção.
        ("kill", vec!["-KILL".into(), "--".into(), format!("-{pid}")])
    }
}

/// Comando que abre a pasta no gerenciador de arquivos do sistema.
pub fn open_folder_command(path: &str) -> Command {
    let mut cmd = Command::new(open_folder_program());
    cmd.arg(path);
    cmd
}

fn open_folder_program() -> &'static str {
    if cfg!(windows) {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    }
}

/// Nome do script de setup do ambiente de IA para esta plataforma.
pub fn setup_script_name() -> &'static str {
    if cfg!(windows) {
        "setup-sidecar.ps1"
    } else {
        "setup-sidecar.sh"
    }
}

/// Comando que roda o script de setup em modo NÃO interativo, com TODA a
/// saída no stdout (o chamador redireciona para o log tailado na UI).
pub fn setup_command(script: &Path) -> tokio::process::Command {
    if cfg!(windows) {
        // `-Command` com `*>&1` faz TODA a saída do PowerShell (inclusive o
        // Write-Host, que é o stream de Information) ir para o stdout
        // capturado - senão as linhas coloridas de progresso se perderiam.
        let ps_cmd = format!("& '{}' -Unattended *>&1", script.display());
        let mut cmd = tokio::process::Command::new("powershell");
        cmd.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"])
            .arg(ps_cmd);
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd
    } else {
        let mut cmd = tokio::process::Command::new("bash");
        cmd.arg(script).arg("--unattended");
        cmd
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let m: HashMap<String, String> =
            pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        move |k| m.get(k).cloned()
    }

    #[cfg(windows)]
    #[test]
    fn data_dir_windows_usa_localappdata() {
        let d = data_dir_from(env(&[("LOCALAPPDATA", r"C:\Users\x\AppData\Local")]));
        assert_eq!(d, Some(PathBuf::from(r"C:\Users\x\AppData\Local\USKMaker")));
        assert_eq!(data_dir_from(env(&[])), None);
    }

    #[cfg(not(windows))]
    #[test]
    fn data_dir_unix_prefere_xdg_data_home() {
        let d = data_dir_from(env(&[("XDG_DATA_HOME", "/data"), ("HOME", "/home/x")]));
        assert_eq!(d, Some(PathBuf::from("/data/USKMaker")));
    }

    #[cfg(not(windows))]
    #[test]
    fn data_dir_unix_cai_para_local_share() {
        let d = data_dir_from(env(&[("HOME", "/home/x")]));
        assert_eq!(d, Some(PathBuf::from("/home/x/.local/share/USKMaker")));
        // vazio ou relativo não vale (especificação XDG)
        let d = data_dir_from(env(&[("XDG_DATA_HOME", ""), ("HOME", "/home/x")]));
        assert_eq!(d, Some(PathBuf::from("/home/x/.local/share/USKMaker")));
        let d = data_dir_from(env(&[("XDG_DATA_HOME", "rel/dir"), ("HOME", "/home/x")]));
        assert_eq!(d, Some(PathBuf::from("/home/x/.local/share/USKMaker")));
        assert_eq!(data_dir_from(env(&[])), None);
    }

    #[test]
    fn venv_python_segue_o_layout_do_so() {
        let p = venv_python(Path::new("venv"));
        if cfg!(windows) {
            assert_eq!(p, Path::new("venv").join("Scripts").join("python.exe"));
        } else {
            assert_eq!(p, Path::new("venv").join("bin").join("python"));
        }
    }

    #[test]
    fn exe_name_so_poe_extensao_no_windows() {
        assert_eq!(exe_name("ffmpeg"), if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" });
    }

    #[test]
    fn kill_tree_mata_a_arvore_inteira() {
        let (prog, args) = kill_tree_args(1234);
        if cfg!(windows) {
            assert_eq!(prog, "taskkill");
            assert_eq!(args, ["/PID", "1234", "/T", "/F"]);
        } else {
            // grupo (-pgid), não só o processo
            assert_eq!(prog, "kill");
            assert_eq!(args, ["-KILL", "--", "-1234"]);
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn kill_tree_derruba_filho_do_grupo() {
        // Cenário real do cancelamento: o sidecar (sh) tem um filho (sleep)
        // que NÃO pode sobreviver ao cancelamento.
        let mut cmd = tokio::process::Command::new("sh");
        cmd.args(["-c", "sleep 30 & echo $!; wait"])
            .stdout(std::process::Stdio::piped());
        detach_process_group(&mut cmd);
        let mut child = cmd.spawn().unwrap();
        let pid = child.id().unwrap();

        use tokio::io::AsyncBufReadExt;
        let mut lines = tokio::io::BufReader::new(child.stdout.take().unwrap()).lines();
        let grandchild: u32 = lines.next_line().await.unwrap().unwrap().trim().parse().unwrap();

        assert!(kill_tree_command(pid).status().unwrap().success());
        child.wait().await.unwrap();

        // o neto também morreu: kill -0 falha para PID inexistente
        let mut alive = true;
        for _ in 0..50 {
            let st = Command::new("kill").args(["-0", &grandchild.to_string()]).status().unwrap();
            if !st.success() {
                alive = false;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        assert!(!alive, "o processo filho sobreviveu ao cancelamento");
    }
}

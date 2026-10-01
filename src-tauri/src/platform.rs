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
//
// No AppImage há um detalhe a mais: ver `appimage_env_fixes`.

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
    for (k, v) in appimage_env_fixes(|k| std::env::var(k).ok()) {
        match v {
            Some(v) => cmd.env(k, v),
            None => cmd.env_remove(k),
        };
    }
    cmd
}

/// Variáveis que o AppRun do AppImage (hook do linuxdeploy-plugin-gtk)
/// exporta para o GTK EMBUTIDO no AppImage achar temas, módulos e schemas.
const APPIMAGE_GTK_VARS: &[&str] = &[
    "GTK_DATA_PREFIX",
    "GTK_THEME",
    "GSETTINGS_SCHEMA_DIR",
    "GI_TYPELIB_PATH",
    "GIO_MODULE_DIR",
    "GTK_EXE_PREFIX",
    "GTK_PATH",
    "GTK_IM_MODULE_FILE",
    "GDK_PIXBUF_MODULE_FILE",
];

/// Ajustes de ambiente para um programa DO SISTEMA iniciado de dentro do
/// AppImage: `None` = remover a variável, `Some` = novo valor. Fora do
/// AppImage (sem `APPIMAGE`/`APPDIR`), nada muda.
///
/// Por que: o AppRun exporta as variáveis acima apontando para o GTK que o
/// AppImage leva junto, e todo processo filho as herda. O `xdg-open` abre o
/// gerenciador de arquivos do usuário - um programa GTK da DISTRO, que
/// carregaria os módulos GIO/GTK do AppImage (compilados contra a glib do
/// Ubuntu 22.04 do CI) em vez dos dele. Do `XDG_DATA_DIRS` só saem as
/// entradas dentro do AppImage; o resto é do usuário.
fn appimage_env_fixes(get: impl Fn(&str) -> Option<String>) -> Vec<(&'static str, Option<String>)> {
    let non_empty = |k: &str| get(k).filter(|v| !v.is_empty());
    let (Some(_), Some(appdir)) = (non_empty("APPIMAGE"), non_empty("APPDIR")) else {
        return Vec::new();
    };
    let mut fixes: Vec<(&'static str, Option<String>)> =
        APPIMAGE_GTK_VARS.iter().map(|k| (*k, None)).collect();
    if let Some(dirs) = non_empty("XDG_DATA_DIRS") {
        let kept: Vec<&str> = dirs
            .split(':')
            .filter(|d| !d.is_empty() && !d.starts_with(appdir.as_str()))
            .collect();
        fixes.push(("XDG_DATA_DIRS", (!kept.is_empty()).then(|| kept.join(":"))));
    }
    fixes
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

    #[test]
    fn appimage_env_fixes_fora_do_appimage_nao_mexe_em_nada() {
        let fixes = appimage_env_fixes(env(&[
            ("GTK_THEME", "Adwaita:dark"),
            ("XDG_DATA_DIRS", "/usr/share"),
        ]));
        assert!(fixes.is_empty());
    }

    #[test]
    fn appimage_env_fixes_limpa_o_gtk_do_appimage() {
        let fixes = appimage_env_fixes(env(&[
            ("APPIMAGE", "/home/x/USKMaker.AppImage"),
            ("APPDIR", "/tmp/.mount_USKabc"),
            ("XDG_DATA_DIRS", "/tmp/.mount_USKabc/usr/share:/usr/share:/home/x/.local/share/flatpak/exports/share"),
        ]));
        let get = |k: &str| fixes.iter().find(|(n, _)| *n == k).map(|(_, v)| v.clone());
        for k in APPIMAGE_GTK_VARS {
            assert_eq!(get(k), Some(None), "{k} deveria ser removida");
        }
        // só a entrada do AppImage sai; a ordem do resto se mantém
        assert_eq!(
            get("XDG_DATA_DIRS"),
            Some(Some("/usr/share:/home/x/.local/share/flatpak/exports/share".to_string()))
        );
    }

    #[test]
    fn appimage_env_fixes_remove_xdg_data_dirs_que_so_tinha_o_appimage() {
        let fixes = appimage_env_fixes(env(&[
            ("APPIMAGE", "/a.AppImage"),
            ("APPDIR", "/tmp/.mount_x"),
            ("XDG_DATA_DIRS", "/tmp/.mount_x/usr/share:"),
        ]));
        assert!(fixes.contains(&("XDG_DATA_DIRS", None)));
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

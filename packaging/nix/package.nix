{
  lib,
  stdenv,
  python3,
  makeWrapper,
  mpv,
  yt-dlp,
  ffmpeg,
  playwright-driver,
  # Headless-Chromium fallback for JS-only players (opt-in: large closure).
  withBrowser ? false,
  # Merging streams for 📥 upload and ⏺ record/clip.
  withFfmpeg ? true,
}:

let
  pythonEnv = python3.withPackages (
    ps:
    [
      ps.aiogram
      ps.aiohttp
      ps.curl-cffi
      ps.pycryptodomex
    ]
    ++ lib.optional withBrowser ps.playwright
  );
  runtimePath = lib.makeBinPath (
    [
      mpv
      yt-dlp
    ]
    ++ lib.optional withFfmpeg ffmpeg
  );
in
stdenv.mkDerivation {
  pname = "tg-mpv-bot";
  version = (builtins.fromTOML (builtins.readFile ../../pyproject.toml)).project.version;

  src = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../bot.py
      ../../src
      ../../assets
      ../../scripts
    ];
  };

  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/tg-mpv-bot
    cp -r bot.py src assets scripts $out/share/tg-mpv-bot/
    makeWrapper ${pythonEnv.interpreter} $out/bin/tg-mpv-bot \
      --add-flags $out/share/tg-mpv-bot/bot.py \
      --prefix PATH : ${runtimePath} ${
        lib.optionalString withBrowser "--set-default PLAYWRIGHT_BROWSERS_PATH ${playwright-driver.browsers}"
      }
    runHook postInstall
  '';

  meta = {
    description = "Telegram remote control for mpv";
    homepage = "https://github.com/antlis/tg-mpv-bot";
    license = lib.licenses.mit;
    mainProgram = "tg-mpv-bot";
    platforms = lib.platforms.linux;
  };
}

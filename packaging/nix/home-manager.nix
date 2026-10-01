self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.tg-mpv-bot;
  inherit (lib) mkEnableOption mkOption mkIf types;
in
{
  options.services.tg-mpv-bot = {
    enable = mkEnableOption "tg-mpv-bot, a Telegram remote control for mpv";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.system}.default;
      defaultText = "tg-mpv-bot flake package";
    };

    environmentFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = "/home/me/.config/environment.d/99-tg-mpv-bot.conf";
      description = ''
        File with BOT_TOKEN (and optionally ALLOWED_USERS), kept out of the
        Nix store. Pass it as a string, not a Nix path literal.
      '';
    };

    settings = mkOption {
      type = types.attrsOf types.str;
      default = { };
      example = {
        DISPLAY = ":0";
        PRE_PLAY_HOOK = "i3-msg workspace 10";
        YTDL_COOKIES_BROWSER = "firefox";
      };
      description = "Extra environment variables (see .env.example).";
    };
  };

  config = mkIf cfg.enable {
    systemd.user.services.tg-mpv-bot = {
      Unit = {
        Description = "tg-mpv-bot — Telegram mpv remote control";
        After = [ "network-online.target" ];
        Wants = [ "network-online.target" ];
      };
      Service = {
        ExecStart = lib.getExe cfg.package;
        Restart = "on-failure";
        RestartSec = 5;
        # Shared with the Docker deployment so only one of them can poll.
        Environment =
          lib.mapAttrsToList (k: v: "${k}=${v}") (
            { LOCK_FILE = "%t/tg-mpv-bot.lock"; } // cfg.settings
          );
        EnvironmentFile = mkIf (cfg.environmentFile != null) (toString cfg.environmentFile);
      };
      Install.WantedBy = [ "default.target" ];
    };
  };
}

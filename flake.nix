{
  description = "tg-mpv-bot — Telegram remote control for mpv";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        tg-mpv-bot = pkgs.callPackage ./packaging/nix/package.nix { };
        with-browser = tg-mpv-bot.override { withBrowser = true; };
        default = tg-mpv-bot;
      });

      overlays.default = final: _prev: {
        tg-mpv-bot = final.callPackage ./packaging/nix/package.nix { };
      };

      homeManagerModules.default = import ./packaging/nix/home-manager.nix self;

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            uv
            python3
            mpv
            yt-dlp
            ffmpeg
            ruff
          ];
        };
      });

      checks = forAll (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      });
    };
}

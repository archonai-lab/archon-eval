{
  description = "archon-eval — meeting evaluation CLI";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f system);
    in {
      packages = forAll (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.stdenv.mkDerivation {
            pname = "archon-eval";
            version = "0.1.0";
            src = ./.;
            buildInputs = [ pkgs.python3 ];
            installPhase = ''
              mkdir -p $out/bin
              cp eval.py $out/bin/archon-eval
              chmod +x $out/bin/archon-eval
              patchShebangs $out/bin/archon-eval
            '';
          };
        });

      apps = forAll (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/archon-eval";
        };
      });

      devShells = forAll (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            buildInputs = [ pkgs.python3 ];
            shellHook = ''
              echo "archon-eval dev shell"
              echo "Run: python3 eval.py <command>"
            '';
          };
        });
    };
}

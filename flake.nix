{
  description = "Firefox addons";
  inputs = { nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable"; };

  outputs = { self, nixpkgs, flake-utils, ... }@inputs:
    with nixpkgs.lib; {
      overlays.default = final: prev: {
        firefox-addons = let
          findLicense = spdxId:
            (findFirst (x:
              if (hasAttr "spdxId" x.value) then
                x.value.spdxId == spdxId
              else
                false) { value = licenses.unfree; }
              (attrsToList licenses)).value;

          buildFirefoxAddon = addon:
            makeOverridable ({ pname, version, addonId, url, hash, meta }:
              final.runCommandLocal "firefox-addon-${pname}-${version}" {
                src = final.pkgs.fetchurl { inherit url hash; };
                meta = meta // {
                  platform = platforms.all;
                  license = if hasAttr "license" meta then
                    findLicense meta.license
                  else
                    licenses.unfree;
                };
              } ''
                dst="$out/share/mozilla/extensions/{ec8030f7-c20a-464f-9b0e-13a3a9e97384}"
                mkdir -p "$dst"
                install -v -m644 "$src" "$dst/${addonId}.xpi"
              '') addon;
          firefoxAddon = addon: {
            name = addon.pname;
            value = buildFirefoxAddon addon;
          };
        in pipe ./firefox-addons-generated.json [
          importJSON
          (map firefoxAddon)
          listToAttrs
        ];
      };
    };
}

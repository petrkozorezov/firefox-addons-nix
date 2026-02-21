# TODO add version compatibility check (how???)
{
  description = "Firefox addons";
  inputs = { nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable"; };

  outputs = { self, nixpkgs, flake-utils, ... }@inputs:
    with nixpkgs.lib; let
      buildFirefoxAddon =
        { pname, version, addonId, derivationArgs }: { pkgs, lib, ... }:
          pkgs.runCommandLocal "firefox-addon-${pname}-${version}" derivationArgs  ''
            dst="$out/share/mozilla/extensions/{ec8030f7-c20a-464f-9b0e-13a3a9e97384}"
            mkdir -p "$dst"
            install -v -m644 "$src" "$dst/${addonId}.xpi"
          '';

      findLicense = spdxId:
        (findFirst (x:
          if (hasAttr "spdxId" x.value) then
            x.value.spdxId == spdxId
          else
            false) { value = licenses.unfree; }
          (attrsToList licenses)).value;

      allowMeta = pname: self: meta: allowedMeta: let
        diffF = new: old:
          if isList new
            then let
              listDiff = subtractLists new old;
            in if listDiff == []
              then null
              else listDiff
            else if new == old
              then null
              else new;
        allowValue =
          { name, value }: let
            diff = diffF value meta.${name};
          in
            diff == null || throw "firefox addon '${pname}' has unallowed meta '${name}': ${builtins.toJSON diff}\nall addon meta: ${generators.toPretty {} meta}";
      in
        assert all allowValue (attrsToList allowedMeta); self;

      buildFirefoxAddonFromStore =
        { pname, version, addonId, url, hash, meta }: args: let
          pkg =
            buildFirefoxAddon {
              inherit pname version addonId;
              derivationArgs = {
                src = args.pkgs.fetchurl { inherit url hash; };
                passthru.allow = allowMeta pname pkg meta;
                meta = {
                  addonName = pname;
                  platform = platforms.all;
                  license = if hasAttr "license" meta then
                    findLicense meta.license
                  else
                    licenses.unfree;
                };
              };
            } args;
        in pkg;
    in {
      lib = { inherit buildFirefoxAddon; };
      overlays.default = final: prev: {
        firefox-addons = pipe ./firefox-addons-generated.json [
          importJSON
          (map (addon: {
            name = addon.pname;
            value = makeOverridable (buildFirefoxAddonFromStore addon) final;
          }))
          listToAttrs
        ];
      };
    };
}

# TODO add version compatibility check (how???)
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

          expectMeta = pname: self: meta: expectedMeta: let
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
            expectValue =
              { name, value }: let
                diff = diffF value meta.${name};
              in
                diff == null || throw "firefox addon '${pname}' has inexpected meta '${name}': ${builtins.toJSON diff}\nall addon meta: ${generators.toPretty {} meta}";
          in
            assert all expectValue (attrsToList expectedMeta); self;

          buildFirefoxAddon = addon:
            let pkg =
              makeOverridable ({ pname, version, addonId, url, hash, meta }:
                final.runCommandLocal "firefox-addon-${pname}-${version}" {
                  src = final.pkgs.fetchurl { inherit url hash; };
                  passthru.expectMeta = expectMeta pname pkg meta;
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
            in pkg;
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

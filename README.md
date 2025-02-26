# firefox-addons-nix

This is the daily auto updated nix pkgs repository with Firefox add-ons, with over 50 users from [Mozilla API](https://mozilla.github.io/addons-server/topics/api/overview.html).


## Usage with Home-manager

1. follow the [manual](https://nix-community.github.io/home-manager/index.xhtml#ch-nix-flakes) to set up home-manager with flakes
1. add flake input
1. add overlay
1. add extensions to Firefox profile(s)

flake.nix
```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    firefox-addons = {
      url = "github:petrkozorezov/firefox-addons-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, home-manager, firefox-addons-nix, ... }: {
    homeConfigurations.my-user = home-manager.lib.homeManagerConfiguration {
      pkgs = import nixpkgs {
        system   = "x86_64-linux";
        overlays = [ firefox-addons.overlays.default ];
      };
      modules = [
        { pkgs, ... }: {
          programs.firefox = {
            enable = true;
            profiles.myprofile.extensions = with pkgs.firefox-addons; [
              ublock-origin
              # A check-based installation that prevents sudden changes to add-on permissions and other params.
              (tree-style-tab.allow {
                # Only those parameters that are in this list will be checked.
                permissions = [ "activeTab" "contextualIdentities" "cookies" "menus" "menus.overrideContext" "notifications" "search" "sessions" "storage" "tabs" "theme" ];
              })
              (auto-tab-discard.allow {
                permissions         = [ "idle" "storage" "contextMenus" "notifications" "alarms" "*://*/*" "<all_urls>" ];
                hostPermissions     = [];
                optionalPermissions = [];
                promotedCategory    = "recommended";
                requiresPayment     = false;
              })
            ];
          };
        }
      ];
    };
  };
}
```

## Why only add-ons with over 50 users?

To reduce nix memory usage and because the API now has a limit on the number of search results (30000 elements).
At the time of writing, there were:

```
overall: 554051
>   0 users: 136388
>   1 users:  61197
>  10 users:  27067
>  25 users:  18361
>  50 users:  13570 *
> 100 users:   9975
```

# base

Baseline OS setup every other role depends on. Always applied (no enable
flag) -- first role in `site.yml`.

- Installs core apt packages (curl, git, htop, vim) plus the interactive
  shell stack (zsh, zsh-autosuggestions, zsh-syntax-highlighting).
- Installs Oh My Zsh and the Powerlevel10k theme, both pinned to a fixed
  git ref -- not a moving branch -- so a prompt update is a deliberate SHA
  bump here, not a surprise on the next apply.
- Adds `admin_user` to the `www-data` group and sets zsh as its login
  shell.

Requires running as root (`become: true`); asserts this explicitly rather
than failing with a confusing permission error partway through.

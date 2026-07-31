# Home Infrastructure

Infrastructure as Code configuration for the home server.

## Current scope

- Ansible inventory
- Server connectivity check
- Base package management

## Usage

Check server connectivity:

```bash
ansible home_servers -m ansible.builtin.ping

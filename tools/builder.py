import optparse
import datetime
import re
import sys
import json
import os
import subprocess
import time
import requests

UBUNTU_RELEASES = [
    'trusty', # 14.04
    'precise', # 12.04
    'utopic', # 14.10
]

USAGE = """Usage: builder [command] [options]
Command Help: builder [command] --help

Commands:
  version               Print the version and exit
  set-version           Set current version
  build                 Build and release"""

INIT_PATH = 'pritunl/__init__.py'
CHANGES_PATH = 'CHANGES'
DEBIAN_CHANGELOG_PATH = 'debian/changelog'
GITHUB_KEY_PATH = 'tools/github_key'
ARCH_PKGBUILD = 'arch/production/PKGBUILD'
ARCH_DEV_PKGBUILD = 'arch/dev/PKGBUILD'
CENTOS_PKGSPEC = 'centos/pritunl.spec'
CENTOS_DEV_PKGSPEC = 'centos/pritunl-dev.spec'
WWW_DIR = 'www'
STYLES_DIR = 'www/styles'

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


def vagrant_popen(cmd, cwd=None, name='node0'):
    if cwd:
        cmd = 'cd /vagrant/%s; %s' % (cwd, cmd)
    return subprocess.Popen("vagrant ssh --command='%s' %s" % (cmd, name),
        shell=True, stdin=subprocess.PIPE)

def vagrant_check_call(cmd, cwd=None, name='node0'):
    if cwd:
        cmd = 'cd /vagrant/%s; %s' % (cwd, cmd)
    return subprocess.check_call("vagrant ssh --command='%s' %s" % (cmd, name),
        shell=True, stdin=subprocess.PIPE)

def wget(url, cwd=None):
    subprocess.check_call(['wget', url], cwd=cwd)

def rm_tree(path):
    subprocess.check_call(['rm', '-rf', path])

def tar_extract(archive_path, cwd=None):
    subprocess.check_call(['tar', 'xfz', archive_path], cwd=cwd)

def tar_compress(archive_path, in_path, cwd=None):
    subprocess.check_call(['tar', 'cfz', archive_path, in_path], cwd=cwd)


# Get package info
with open(INIT_PATH, 'r') as init_file:
    for line in init_file.readlines():
        line = line.strip()

        if line[:9] == '__title__':
            app_name = line.split('=')[1].replace("'", '').strip()
            pkg_name = app_name.replace('_', '-')

        elif line[:10] == '__author__':
            maintainer = line.split('=')[1].replace("'", '').strip()

        elif line[:9] == '__email__':
            maintainer_email = line.split('=')[1].replace("'", '').strip()

        elif line[:11] == '__version__':
            key, val = line.split('=')
            cur_version = line.split('=')[1].replace("'", '').strip()


# Get github owner and token
with open(GITHUB_KEY_PATH, 'r') as github_key_file:
    github_owner, github_token = github_key_file.read().strip().split(':')


# Parse args
if len(sys.argv) > 1:
    cmd = sys.argv[1]
else:
    cmd = 'version'

parser = optparse.OptionParser(usage=USAGE)
(options, args) = parser.parse_args()

build_num = 0

if cmd == 'version':
    print '%s v%s' % (app_name, cur_version)
    sys.exit(0)


elif cmd == 'set-version':
    new_version = args[1]
    is_snapshot = 'snapshot' in new_version

    # Check for duplicate version
    response = requests.get(
        'https://api.github.com/repos/%s/%s/releases' % (
            github_owner, pkg_name),
        headers={
            'Authorization': 'token %s' % github_token,
            'Content-type': 'application/json',
        },
        data=json.dumps({
            'tag_name': new_version,
            'name': '%s v%s' % (pkg_name, new_version),
            'body': 'Snapshot release',
            'prerelease': True,
        }),
    )

    if response.status_code != 200:
        print 'Failed to get repo releases on github'
        print response.json()
        sys.exit(1)

    for release in response.json():
        if release['tag_name'] == new_version:
            print 'Version already exists in github'
            sys.exit(1)


    # Build webapp
    subprocess.check_call(['grunt', '--ver=%s' % new_version], cwd=STYLES_DIR)
    subprocess.check_call(['grunt'], cwd=WWW_DIR)


    # Generate changelog
    debian_changelog = ''
    changelog_version = None
    release_body = ''
    snapshot_lines = []
    if is_snapshot:
        snapshot_lines.append('Version %s %s' % (
            new_version, datetime.datetime.utcnow().strftime('%Y-%m-%d')))
        snapshot_lines.append('Snapshot release')

    with open(CHANGES_PATH, 'r') as changelog_file:
        for line in snapshot_lines + changelog_file.readlines()[2:]:
            line = line.strip()

            if not line or line[0] == '-':
                continue

            if line[:7] == 'Version':
                if debian_changelog:
                    debian_changelog += '\n -- %s <%s>  %s -0400\n\n' % (
                        maintainer,
                        maintainer_email,
                        date.strftime('%a, %d %b %Y %H:%M:%S'),
                    )

                _, version, date = line.split(' ')
                date = datetime.datetime.strptime(date, '%Y-%m-%d')

                if not changelog_version:
                    changelog_version = version

                debian_changelog += \
                    '%s (%s-%subuntu1) unstable; urgency=low\n\n' % (
                    build_num, pkg_name, version)

            elif debian_changelog:
                debian_changelog += '  * %s\n' % line

                if not is_snapshot and version == new_version:
                    release_body += '* %s\n' % line

        debian_changelog += '\n -- %s <%s>  %s -0400\n' % (
            maintainer,
            maintainer_email,
            date.strftime('%a, %d %b %Y %H:%M:%S'),
        )

    if not is_snapshot and changelog_version != new_version:
        print 'New version does not exist in changes'
        sys.exit(1)

    with open(DEBIAN_CHANGELOG_PATH, 'w') as changelog_file:
        changelog_file.write(debian_changelog)

    if not is_snapshot and not release_body:
        print 'Failed to generate github release body'
        sys.exit(1)
    elif is_snapshot:
        release_body = '* Snapshot release'
    release_body = release_body.rstrip('\n')


    # Update arch package
    pkgbuild_path = ARCH_DEV_PKGBUILD if is_snapshot else ARCH_PKGBUILD
    with open(pkgbuild_path, 'r') as pkgbuild_file:
        pkgbuild_data = re.sub(
            'pkgver=(.*)',
            'pkgver=%s' % new_version,
            pkgbuild_file.read(),
        )

    with open(pkgbuild_path, 'w') as pkgbuild_file:
        pkgbuild_file.write(pkgbuild_data)


    # Update centos package
    pkgspec_path = CENTOS_DEV_PKGSPEC if is_snapshot else CENTOS_PKGSPEC
    with open(pkgspec_path, 'r') as pkgspec_file:
        pkgspec_data = re.sub(
            '%define pkgver (.*)',
            '%%define pkgver %s' % new_version,
            pkgspec_file.read(),
        )

    with open(pkgspec_path, 'w') as pkgspec_file:
        pkgspec_file.write(pkgspec_data)


    # Git commit
    subprocess.check_call(['git', 'reset', 'HEAD', '.'])
    subprocess.check_call(['git', 'add', 'debian/changelog'])
    subprocess.check_call(['git', 'commit', '-m', 'Create new release'])
    subprocess.check_call(['git', 'push'])
    commit_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()
    time.sleep(6)


    # Create release
    response = requests.post(
        'https://api.github.com/repos/%s/%s/releases' % (
            github_owner, pkg_name),
        headers={
            'Authorization': 'token %s' % github_token,
            'Content-type': 'application/json',
        },
        data=json.dumps({
            'tag_name': new_version,
            'name': '%s v%s' % (pkg_name, new_version),
            'body': release_body,
            'prerelease': is_snapshot,
            'target_commitish': commit_hash,
        }),
    )

    if response.status_code != 201:
        print 'Failed to create release on github'
        print response.json()
        sys.exit(1)


elif cmd == 'build':
    build_dir = 'build/%s' % cur_version


else:
    sys.exit(0)
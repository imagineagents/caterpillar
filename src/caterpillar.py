#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Semi-automatically convert Chrome Apps into progressive web apps.

Guides a developer through converting their existing Chrome App into a
progressive web app.
"""

from __future__ import print_function, division, unicode_literals

import argparse
import json
import logging
import os
import random
import shutil
import sys

import bs4
import colorama

import chrome_app.apis
import chrome_app.manifest

# Chrome APIs with polyfills available.
POLYFILLS = {
  'tts',
}

# Manifest filenames.
CA_MANIFEST_FILENAME = chrome_app.manifest.MANIFEST_FILENAME
PWA_MANIFEST_FILENAME = 'manifest.webmanifest'

# Name of the service worker registration script.
REGISTER_SCRIPT_NAME = 'register_sw.js'

# Name of the main service worker script.
SW_SCRIPT_NAME = 'sw.js'

# Name of the service worker static script.
SW_STATIC_SCRIPT_NAME = 'sw_static.js'

# Largest number that the cache version can be.
MAX_CACHE_VERSION = 1000000

# What the converter is called.
CONVERTER_NAME = 'caterpillar'

# Where this file is located (so we can find resources).
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

SW_FORMAT_STRING = """/**
 * @file Service worker generated by {converter_name}.
 */

/**
 * @const Current cache version.
 *
 * Increment this to force cache to clear.
 */
var CACHE_VERSION = {cache_version};

/**
 * @const Object mapping a cache identifier to the actual, versioned cache name.
 */
var CACHES = {{
  'app': 'app-cache-v' + CACHE_VERSION
}};

/**
 * @const An array of filenames of cached files.
 */
var CACHED_FILES = [
  {joined_filepaths}
];

importScripts('{converter_name}/sw_static.js');
"""

def setup_output_dir(input_dir, output_dir, force=False):
  """Sets up the output directory tree.

  Copies all files from the input directory to the output directory, and creates
  a subdirectory for the boilerplate code.

  Args:
    input_dir: String path to input directory.
    output_dir: String path to output directory.
    force: Whether to force overwrite existing output files. Default is False.

  Raises:
    ValueError: Invalid input or output directory.
  """
  # Clean up the directory we want to set up.
  if force:
    logging.debug('Removing output directory tree `%s`.', output_dir)
    shutil.rmtree(output_dir, ignore_errors=True)
  elif os.path.exists(output_dir):
    raise ValueError('Output directory already exists.')

  # Copy files across from the Chrome App.
  logging.debug('Copying input tree `%s` to output tree `%s`.', input_dir,
                output_dir)
  shutil.copytree(input_dir, output_dir)

  # Set up the boilerplate directory.
  conv_path = boilerplate_dir(output_dir)
  logging.debug('Making %s directory `%s`.', CONVERTER_NAME, conv_path)
  os.mkdir(conv_path)

  # Clean up files we don't need in the PWA.
  ca_manifest_path = os.path.join(output_dir, CA_MANIFEST_FILENAME)
  logging.debug('Removing file `%s`.', ca_manifest_path)
  os.remove(ca_manifest_path)

  logging.debug('Finished setting up output directory `%s`.', output_dir)

def polyfill_apis(apis, directory):
  """Copies polyfill scripts into a directory.

  Args:
    apis: List of APIs to polyfill. Strings e.g. 'tts' for the chrome.tts API.
    directory: Directory name to copy into.

  Returns:
    (sorted list of successfully polyfilled APIs,
     sorted list of unsuccessfully polyfilled APIs)
  """
  successful = []
  unsuccessful = []

  for api in apis:
    if api not in POLYFILLS:
      unsuccessful.append(api)
      continue

    polyfill_filename = '{}.polyfill.js'.format(api)
    polyfill_path = os.path.join(SCRIPT_DIR, 'js', 'polyfills',
                                 polyfill_filename)
    destination_path = os.path.join(directory, polyfill_filename)
    shutil.copyfile(polyfill_path, destination_path)
    successful.append(api)

  successful.sort()
  unsuccessful.sort()
  return (successful, unsuccessful)

def ca_to_pwa_manifest(manifest, config):
  """Converts a Chrome App manifest into a progressive web app manifest.

  Args:
    manifest: Manifest dictionary.
    config: Conversion configuration dictionary.

  Returns:
    PWA manifest.
  """
  pwa_manifest = {}
  pwa_manifest['name'] = manifest['name']
  pwa_manifest['short_name'] = manifest.get('short_name', manifest['name'])
  pwa_manifest['lang'] = manifest.get('default_locale', 'en')
  pwa_manifest['splash_screens'] = []
  # TODO(alger): Guess display mode from chrome.app.window.create calls
  pwa_manifest['display'] = 'minimal-ui'
  pwa_manifest['orientation'] = 'any'
  # TODO(alger): Guess start_url from chrome.app.window.create calls
  pwa_manifest['start_url'] = config['start_url']
  # TODO(alger): Guess background/theme colour from the main page's CSS.
  pwa_manifest['theme_color'] = 'white'
  pwa_manifest['background_color'] = 'white'
  pwa_manifest['related_applications'] = []
  pwa_manifest['prefer_related_applications'] = False
  pwa_manifest['icons'] = []
  if 'icons' in manifest:
    for icon_size in manifest['icons']:
      pwa_manifest['icons'].append({
        'src': manifest['icons'][icon_size],
        'sizes': '{0}x{0}'.format(icon_size)
      })

  # TODO(alger): I've only looked at some of the manifest members here; probably
  # a bad idea to ignore the ones that don't copy across. Should give a warning.

  return pwa_manifest

def boilerplate_dir(directory):
  """Gets the path to the converter's boilerplate directory.

  Args:
    directory: Directory path of app.

  Returns:
    Path to boilerplate directory within the given directory.
  """
  return os.path.join(directory, CONVERTER_NAME)

def relative_boilerplate_file_path(filename):
  """Gets the path to a boilerplate file relative to the app root.

  Example:
    relative_boilerplate_file_path('tts.polyfill.js')
      == 'caterpillar/tts.polyfill.js'

  Args:
    filename: Filename of the resource to get the path of.

  Returns:
    Path to the file within the boilerplate directory, relative to the app root.
  """
  return '{}/{}'.format(CONVERTER_NAME, filename)

def polyfill_filename(api):
  """Gets the filename associated with an API polyfill.

  Args:
    api: String name of API.

  Returns:
    Filename of API polyfill.
  """
  return "{}.polyfill.js".format(api)

def inject_script_tag(soup, script_filename, html_filename):
  """Injects a script tag into an HTML document.

  Modifies the provided soup.

  Args:
    soup: BeautifulSoup of the HTML.
    script_filename: Filename of the src of the script tag.
    html_filename: Filename of the HTML document being modified.
  """

  relative_path = relative_boilerplate_file_path(script_filename)
  script = soup.new_tag('script', src=relative_path)
  soup.body.append(script)
  logging.debug('Injected `%s` script into `%s`.', script_filename,
                html_filename)

def inject_tags(html, manifest, polyfills, html_filename):
  """Injects conversion HTML tags into the given HTML.

  Args:
    manifest: Manifest dictionary of the _Chrome App_.
    html: String of HTML of start page.
    polyfills: Polyfilled APIs to add script tags for.
    html_filename: Filename of the start page.

  Returns:
    Modified HTML.
  """
  soup = bs4.BeautifulSoup(html)
  
  # Add manifest link.
  manifest_link = soup.new_tag('link', rel='manifest',
                               href=PWA_MANIFEST_FILENAME)
  soup.head.append(manifest_link)
  logging.debug('Injected manifest link into `%s`.', html_filename)

  # Add polyfills.
  for api in polyfills:
    api_filename = polyfill_filename(api)
    polyfill_script = soup.new_tag('script',
      src=relative_boilerplate_file_path(api_filename))
    # We want to put the polyfill script before the first script tag.
    if soup.body.script:
      soup.body.script.insert_before(polyfill_script)
    else:
      soup.body.append(polyfill_script)
    logging.debug('Injected `%s` script into `%s`.', api_filename,
                  html_filename)

  # Add service worker registration.
  inject_script_tag(soup, REGISTER_SCRIPT_NAME, html_filename)

  # Add meta tags (if applicable).
  for tag in ('description', 'author', 'name'):
    if tag in manifest:
      meta = soup.new_tag('meta', content=manifest[tag])
      meta['name'] = tag
      soup.head.append(meta)
      logging.debug('Injected `%s` meta tag into `%s` with content '
        '`%s`.', tag, html_filename, manifest[tag])

  # Add an encoding meta tag. (Seems to be implicit in Chrome Apps.)
  meta_charset = soup.new_tag('meta', charset='utf-8')
  soup.head.insert(0, meta_charset)
  logging.debug('Injected `charset` meta tag into `%s`.', html_filename)

  return soup.prettify('utf-8')

def insert_todos_into_file(js_path):
  """Inserts TODO comments in a JavaScript file.

  The TODO comments inserted should draw attention to places in the converted
  app that the developer will need to edit to finish converting their app.

  Args:
    js_path: Path to JavaScript file.
  """
  with open(js_path, 'rU') as in_js:
    # This search is very naïve and will only check line-by-line if there
    # are easily spotted Chrome Apps API function calls.
    out_js = []
    for line_no, line in enumerate(in_js):
      api_call = chrome_app.apis.api_function_called(line)
      if api_call is not None:
        # Construct a TODO comment.
        todo = '// TODO: (Caterpillar) Remove {} call.\n'.format(api_call)
        logging.debug('Inserting TODO in `%s:%d`:\n\t%s', js_path, line_no,
                      todo)
        out_js.append(todo)
      out_js.append(line)

  with open(js_path, 'w') as js_file:
    logging.debug('Writing modified file `%s`.', js_path)
    js_file.write(''.join(out_js))

def insert_todos_into_directory(directory):
  """Inserts TODO comments in all JavaScript code in a directory.

  The TODO comments inserted should draw attention to places in the converted
  app that the developer will need to edit to finish converting their app.

  Args:
    directory: Directory filename to insert TODOs into.
  """
  logging.debug('Inserting TODOs.')
  dirwalk = os.walk(directory)
  for (dirpath, _, filenames) in dirwalk:
    for filename in filenames:
      if filename.endswith('.js'):
        path = os.path.join(dirpath, filename)
        insert_todos_into_file(path)

def generate_service_worker(directory):
  """Generates code for a service worker.

  Args:
    directory: Directory this service worker will run in.

  Returns:
    JavaScript string.
  """
  # Get the paths of files we will cache.
  all_filepaths = []
  logging.debug('Looking for files to cache.')
  dirwalk = os.walk(directory)
  for (dirpath, _, filenames) in dirwalk:
    # Add the relative file paths of each file to the filepaths list.
    all_filepaths.extend(
      os.path.relpath(os.path.join(dirpath, filename), directory)
      for filename in filenames)
  logging.debug('Cached files:\n\t%s', '\n\t'.join(all_filepaths))
  # Format the file paths as JavaScript strings.
  all_filepaths = ['"{}"'.format(fp) for fp in all_filepaths]

  logging.debug('Generating service worker.')

  sw_js = SW_FORMAT_STRING.format(
    converter_name=CONVERTER_NAME,
    cache_version=random.randrange(MAX_CACHE_VERSION),
    joined_filepaths=',\n  '.join(all_filepaths)
  )
  return sw_js

def copy_script(script, directory):
  """Copies a script into the given directory.

  Args:
    script: Caterpillar boilerplate JavaScript filename.
    directory: Directory to copy into.
  """
  path = os.path.join(SCRIPT_DIR, 'js', script)
  new_path = os.path.join(boilerplate_dir(directory), script)
  logging.debug('Writing `%s` to `%s`.', path, new_path)
  shutil.copyfile(path, new_path)

def add_service_worker(directory):
  """Adds service worker scripts to the given directory.

  Args:
    directory: Directory name to add service worker scripts to.
  """
  # We have to copy the other scripts before we generate the service worker
  # caching script, or else they won't be cached.
  copy_script(REGISTER_SCRIPT_NAME, directory)
  copy_script(SW_STATIC_SCRIPT_NAME, directory)

  sw_js = generate_service_worker(directory)

  # We can now write the service worker. Note that it must be in the root.
  sw_path = os.path.join(directory, SW_SCRIPT_NAME)
  logging.debug('Writing service worker to `%s`.', sw_path)
  with open(sw_path, 'w') as sw_file:
    sw_file.write(sw_js)

def convert_app(input_dir, output_dir, config, force=False):
  """Converts a Chrome App into a progressive web app.

  Args:
    input_dir: String path to input directory.
    output_dir: String path to output directory.
    config: Configuration dictionary.
    force: Whether to force overwrite existing output files. Default is False.
  """
  # Copy everything across to the output directory.
  try:
    setup_output_dir(input_dir, output_dir, force)
  except ValueError as e:
    logging.error(e.message)
    return

  # Initial pass to detect and polyfill Chrome Apps APIs.
  apis = chrome_app.apis.app_apis(output_dir)
  if apis:
    logging.info('Found Chrome APIs: %s', ', '.join(apis))

  conv_dir = boilerplate_dir(output_dir)
  successful, unsuccessful = polyfill_apis(apis, conv_dir)
  if successful:
    logging.info('Polyfilled Chrome APIs: %s', ', '.join(successful))
  if unsuccessful:
    logging.warning(
      'Could not polyfill Chrome APIs: %s', ', '.join(unsuccessful))

  # Read in and check the manifest file. Generate the new manifest from that.
  try:
    manifest = chrome_app.manifest.get(input_dir)
  except ValueError as e:
    logging.error(e.message)
    return

  try:
    chrome_app.manifest.verify(manifest)
  except ValueError as e:
    logging.error(e.message)
    return

  # Convert the Chrome app manifest into a progressive web app manifest.
  pwa_manifest = ca_to_pwa_manifest(manifest, config)
  pwa_manifest_path = os.path.join(output_dir, PWA_MANIFEST_FILENAME)
  with open(pwa_manifest_path, 'w') as pwa_manifest_file:
    json.dump(pwa_manifest, pwa_manifest_file, indent=4, sort_keys=True)
  logging.debug('Wrote `%s` to `%s`.', PWA_MANIFEST_FILENAME, pwa_manifest_path)

  # Inject tags into the HTML of the start file.
  start_path = os.path.join(output_dir, pwa_manifest['start_url'])
  with open(start_path, 'r') as start_file:
    start_html = inject_tags(start_file.read(), manifest, successful,
                             start_path)

  # Write the HTML back to the output directory.
  logging.debug('Writing edited and prettified start HTML to `%s`.', start_path)
  with open(start_path, 'w') as start_file:
    start_file.write(start_html)

  # Insert TODO comments into the output code.
  insert_todos_into_directory(output_dir)

  # Copy service worker scripts.
  add_service_worker(output_dir)

  logging.info('Conversion complete.')

def print_default_config():
  """Prints a default configuration file to stdout."""
  default_config = {
    'start_url': 'index.html',
    'name': 'My Chrome App',
    'id': -1,
    'root': '',
    'boilerplate-dir': '/caterpillar/',
    'update-uris': True,
    'enable-watch-sw': True,
    'report-path': 'caterpillar-report/'
  }

  json.dump(default_config, sys.stdout, sort_keys=True, indent=2)

class Formatter(logging.Formatter):
  """Caterpillar logging formatter.

  Adds color to the logged information.
  """
  def format(self, record):
    style = ''
    if record.levelno == logging.ERROR:
      style = colorama.Fore.RED + colorama.Style.BRIGHT
    elif record.levelno == logging.WARNING:
      style = colorama.Fore.YELLOW + colorama.Style.BRIGHT
    elif record.levelno == logging.INFO:
      style = colorama.Fore.BLUE
    elif record.levelno == logging.DEBUG:
      style = colorama.Fore.CYAN + colorama.Style.DIM

    return style + super(Formatter, self).format(record)

def main():
  """Executes the script and handles command line arguments."""
  # Set up parsers, then parse the command line arguments.
  desc = 'Semi-automatically convert Chrome Apps into progressive web apps.'
  parser = argparse.ArgumentParser(description=desc)
  parser.add_argument('-v', '--verbose', help='Verbose logging',
                      action='store_true')
  subparsers = parser.add_subparsers(dest='mode')

  parser_convert = subparsers.add_parser(
    'convert', help='Convert a Chrome App into a progressive web app.')
  parser_convert.add_argument('input', help='Chrome App input directory')
  parser_convert.add_argument(
    'output', help='Progressive web app output directory')
  parser_convert.add_argument('-c', '--config', help='Configuration file',
                              required=True, metavar='config')
  parser_convert.add_argument('-f', '--force', help='Force output overwrite',
                              action='store_true')

  parser_config = subparsers.add_parser(
    'config', help='Print a default configuration file to stdout.')

  args = parser.parse_args()

  # Set up logging.
  logging_level = logging.DEBUG if args.verbose else logging.INFO
  logging.root.setLevel(logging_level)
  colorama.init(autoreset=True)
  logging_format = ':%(levelname)s:  \t%(message)s'
  formatter = Formatter(logging_format)
  handler = logging.StreamHandler(sys.stdout)
  handler.setFormatter(formatter)
  logging.root.addHandler(handler)

  # Main program.
  if args.mode == 'config':
    print_default_config()
  elif args.mode == 'convert':
    with open(args.config) as config_file:
      config = json.load(config_file)
    convert_app(args.input, args.output, config, args.force)

if __name__ == '__main__':
  sys.exit(main())

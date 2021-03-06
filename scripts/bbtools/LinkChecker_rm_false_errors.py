#!/usr/bin/env python

#
# Copyright (C) 2010-2018 ABINIT Group (Jean-Michel Beuken)
#
# This file is part of the ABINIT software package. For license information,
# please see the COPYING file in the top-level directory of the ABINIT source
# distribution.
#

#
# 'false' errors elimination in the 'linkchecker_ext.log' generated by
# a script located on ref slave ( abiref:~buildbot/bin/LinkChecker3.sh )
#

from __future__ import unicode_literals, division, print_function, absolute_import

import sys
import os
import re
from lxml import etree

# ---------------------------------------------------------------------------- #

#
# Functions
#

#all_tags = [ 'url','name','parent','realurl','extern','dlsize','checktime','level','infos','valid' ]
#printable_tags = [ 'url', 'name', 'parent', 'realurl', 'valid' ]

server="http://localhost:8000"

def rm_server(keyword):
  if keyword.startswith(server):
     return keyword[len(server):]

# ---------------------------------------------------------------------------- #

# Types of error :
#    Error: 403 Forbidden'
#    Error: 404 Not Found'
#    Error: 504 Gateway Time-out'
#    Error: 502 Bad Gateway'
#    Error: ReadTimeout:'
#    ConnectionError: ('Connection aborted.'

no_error_list = [
    { 'url'  : re.compile('(doi|aps).org'),
      'info' : re.compile('^Redirected'),
      'valid': re.compile('^403 Forbidden')
    },
]

# ---------------------------------------------------------------------------- #

#
# Main program
#
def main(filename,home_dir=""):
  from os.path import join as pj

  # Check if we are in the top of the ABINIT source tree
  my_name = os.path.basename(__file__) + ".main"
  if ( not os.path.exists(pj(home_dir,"configure.ac")) or
       not os.path.exists(pj(home_dir, "src/98_main/abinit.F90")) ):
    print("%s: You must be in the top of an ABINIT source tree." % my_name)
    print("%s: Aborting now." % my_name)
    sys.exit(1)

  #
  tree = etree.parse(filename)
  
  rc=0
  for child in tree.xpath("/linkchecker/urldata"):
  
    for no_error in no_error_list:
  
      norc = True  # True : we consider that this is not an error
  
      url_rc = None
      url = child.find('url')
      url_rc = no_error['url'].search(url.text)
      norc = norc and ( url_rc != None )
  
      info_rc = None
      info=child.find('infos/info')
      if info is not None:
        try:
         info_rc=no_error['info'].search(info.text)
         norc = norc and ( info_rc != None )
        except:
         pass
  
      valid_rc = None
      valid=child.find('valid').get("result")
      valid_rc=no_error['valid'].search(valid)
      norc = norc and ( valid_rc != None )
  
      if url_rc != None and info_rc != None and valid_rc != None:
          break

      if not norc :
          # found a true error... : reporting on bb
          rc += 1
          name=child.find('name')
          parent=child.find('parent')
          realurl=child.find('realurl')
          print("{0:12} {1}".format('URL',url.text))
          print("{0:12} {1}".format('Name',name.text))
          print("{0:12} {1}, line {2}".format('Parent URL',rm_server(parent.text),parent.get('line')))
          print("{0:12} {1}".format('Real URL',realurl.text))
          print("{0:12} {1}".format('Result',valid))
          print('--------------------')
          break

  return rc

# ---------------------------------------------------------------------------- #

if __name__ == "__main__":
  
  try:
    filename = sys.argv[1]
  except:
    print("filename missing...")
    sys.exit(1)

  if len(sys.argv) == 2:
    home_dir = "."
  else:
    home_dir = sys.argv[2]

  exit_status = main(filename,home_dir)
  sys.exit(exit_status)

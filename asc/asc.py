#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# This file is part of Red Alien.

#    Red Alien is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    Red Alien is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.

#    You should have received a copy of the GNU General Public License
#    along with Red Alien.  If not, see <http://www.gnu.org/licenses/>.

''' Main compiler/decompiler module '''

import sys
import os
import argparse
import re
from . import pokecommands as pk
from . import text_translate
from pprint import pprint
from .preprocessor import preprocess, remove_comments

MAX_NOPS = 10
USING_WINDOWS = (os.name == 'nt')
USING_DYNAMIC = False
END_COMMANDS = ["end", "jump", "return"]
END_HEX_COMMANDS = [0xFF]

OPERATORS_LIST = ("==", "!=", "<=", ">=", "<", ">")

OPERATORS = {"==": "1", "!=": "5", "<": "0", ">": "2",
             "<=": "3", ">=": "4"}
OPPOSITE_OPERATORS = {"==": "!=", "!=": "==", "<": ">=", ">": "<=",
                      "<=": ">", ">=": "<"}

# windows builds are frozen
if getattr(sys, 'frozen', False):
    data_path = os.path.join(os.path.dirname(sys.executable), "data")
else:
    data_path = os.path.join(os.path.dirname(__file__), "data")

QUIET = False
VERBOSE = 0
def debug(*args):
    if not QUIET:
        print(*args)

def vdebug(*args):
    if VERBOSE:
        print(*args)

def pdebug(*args):
    if not QUIET:
        pprint(*args)

def vpdebug(*args):
    if VERBOSE:
        pprint(*args)

def hprint(bytes_):
    def pad(s):
        return "0" + s if len(s) == 1 else s

    for b in bytes_:
        print(pad(hex(b)[2:]), end=" ")
    print("")

def phdebug(bytes_):
    if not QUIET:
        hprint(bytes_)

def dirty_compile(text_script, include_path):
    text_script = remove_comments(text_script)
    text_script = re.sub("^[ \t]*", "", text_script, flags=re.MULTILINE)
    text_script = preprocess(text_script, include_path)
    text_script = regexps(text_script)
    text_script = compile_clike_blocks(text_script)
    return text_script

def regexps(text_script):
    ''' Part of the preparsing '''
    # FIXME: We beak line numbers everywhere :(
    # XSE 1.1.1 like msgboxes
    text_script = re.sub(r"msgbox (.+?) (.+?)", r"msgbox \1\ncallstd \2",
                         text_script)
    text_script = text_script.replace("goto", "jump")

    # Join lines ending with \
    text_script = re.sub("\\\\\\n", r"", text_script)
    for label in re.findall(r"@\S+", text_script, re.MULTILINE):
        if not "#org "+label in text_script:
            raise Exception("ERROR: Unmatched @ label %s" % label)
    return text_script

# TODO: better names for these functions
def grep_part(text, start_pos, open_, close):
    start_pos = start_pos + text[start_pos:].find(open_) + 1
    s = -1
    i = 0
    for i, char in enumerate(text[start_pos:]):
        if char == close:
            s += 1
        elif char == open_:
            s -= 1
        if s == 0:
            break
    else:
        raise Exception("No matching " + close + " found")
    end_pos = start_pos + i
    return start_pos, end_pos

def grep_statement(text, name):
    pos = re.search(name + r".?\(", text).start()
    condition_start, condition_end = grep_part(text, pos, "(", ")")
    condition = text[condition_start:condition_end]
    body_start, body_end = grep_part(text, pos, "{", "}")
    body = text[body_start:body_end]
    return (pos, body_end+1, condition, body)

def compile_while(text_script, level):
    pos, end_pos, condition, body = grep_statement(text_script, "while")
    body = compile_clike_blocks(body, level+1)
    # Any operator in the condition expression
    part = ":while_start" + str(level) + '\n'
    for operator in OPERATORS_LIST:
        if operator in condition:
            var, constant = condition.split(operator)
            part += "compare " + var.strip() + " " + constant.strip() + "\n"
            part += ("if " + OPPOSITE_OPERATORS[operator] +
                     " jump :while_end" + str(level) + '\n')
            break
    else:
        # We are checking a flag
        if condition[0] == "!":
            flag = condition[1:]
            operator = "=="
        else:
            flag = condition
            operator = "!="
        part += "checkflag " + flag + "\n"
        part += "if " + operator + " jump :while_end" + str(level) + '\n'
    part += body
    part += "\njump :while_start" + str(level)
    part += "\n:while_end" + str(level) + "\n"
    # hack
    if text_script[pos] == "\n":
        pos -= 1
    if text_script[end_pos] == "\n":
        end_pos += 1
    text_script = text_script[:pos] + part + text_script[end_pos:]
    return text_script

def compile_if(text_script, level):
    pos, end_pos, condition, body = grep_statement(text_script, "if")
    body = compile_clike_blocks(body)
    have_else = re.match("\\selse\\s*?{", text_script[end_pos:])
    # Any operator in the condition expression
    part = ''
    for operator in OPERATORS_LIST:
        if operator in condition:
            var, constant = condition.split(operator)
            part += "compare " + var.strip() + " " + constant.strip() + "\n"
            part += ("if " + OPPOSITE_OPERATORS[operator] +
                     " jump :if_end" + str(level) + '\n')
            break
    else:
        # We are checking a flag
        if condition[0] == "!":
            flag = condition[1:]
            operator = "=="
        else:
            flag = condition
            operator = "!="
        part += "checkflag " + flag + "\n"
        part += "if " + operator + " jump :if_end" + str(level) + "\n"
    part += body
    if have_else:
        else_body_start, else_body_end = grep_part(text_script,
                                                   end_pos, "{", "}")
        else_body = text_script[else_body_start:else_body_end]
        else_body = compile_clike_blocks(else_body, level+1)
        part += "\njump :else_end" + str(level) + "\n"
        part += ":if_end" + str(level) + "\n"
        part += else_body + '\n:else_end' + str(level) + '\n'
        end_pos = else_body_end + 1

    else:
        part += "\n:if_end" + str(level)
    text_script = text_script[:pos] + part + text_script[end_pos:]
    return text_script

def compile_clike_blocks(text_script, level=0):
    ''' The awesome preparsing (actually you could call it compiling)
        of cool stuctures '''
    # FIXME: this is crap really

    # Okay, so this is what we want:
    # 1. ----------- while -----------
    # while (<expression>) {
    #   <command>
    #   . . .
    # }
    #
    # 2. ----------- if/else -----------
    # if (<expression>) {
    #   <command>
    #   . . .
    # } [else {
    #   <command>
    #   . . .
    # }]
    #
    # 3. ----------- expressions -----------
    # (<var num> <operator> <literal num>)
    # or
    # (<flag num>)

    while re.search(r"while.?\(", text_script):
        text_script = compile_while(text_script, level)
        level += 1

    # I'll refactor this one day, I promise =P
    while re.search(r"if.?\(", text_script):
        text_script = compile_if(text_script, level)
        level += 1

    text_script = text_script.strip("\n")
    return text_script

def asm_parse(text_script, end_commands=("end", "softend"),
        cmd_table=pk.pkcommands):
    ''' The basic language preparsing function '''
    list_script = text_script.split("\n")
    org_i = -1
    dyn = (False, 0)
    parsed_list = []

    for num, line in enumerate(list_script):
        line = line.rstrip(" ")
        if line == "":
            continue
        # Labels for goto's
        if line[0] == ":":
            parsed_list[org_i].append([line])
            continue

        words = line.split()
        command = words[0]
        args = words[1:]

        if command not in cmd_table:
            error = ("ERROR: command not found in line " + str(num+1) + ":" +
                     "\n" + str(line))
            raise Exception(error)
        # if command has args
        if "args" in cmd_table[command]:
            arg_num = len(cmd_table[command]["args"][1])
        else:
            arg_num = 0

        if len(args) != arg_num and command != '=':
            error = ("ERROR: wrong argument number in line " + str(num+1) + '\n'
                     + line + '\n' + str(args) + '\n' + "Args given: " +
                     str(len(args)) + '\n' + "Context:\n")
            for line_num in range(num-3, num+6):
                error += "    " + list_script[line_num] + "\n"
            args = cmd_table[command]['args']
            if args and args[0]:
                error += "Args needed: " + args[0] + " " + str(args[1])
            raise Exception(error)

        else:
            if command == "#org":
                org_i += 1
                offset = args
                parsed_list.append(offset)

            elif command == "#dyn" or command == "#dynamic":
                if len(args) == 1:
                    global USING_DYNAMIC
                    USING_DYNAMIC = True
                    dyn = (True, args[0])
                else:
                    raise Exception("ERROR: #dyn/#dynamic statement needs an address argument")

            elif org_i == -1:
                raise Exception("ERROR: No #org found on line " + str(num))

            elif command in end_commands or words == ["#raw", "0xFE"]:
                parsed_list[org_i].append(words)

            elif command == "=":
                parsed_list[org_i].append(line)

            elif command == "if":
                if len(args) != 3:
                    error = ("ERROR: syntax error on line " + str(num + 1) +
                             "\nArgument number wrong in 'if'")
                    raise Exception(error)
                if args[1] == "jump":
                    branch = "jumpif"
                elif args[1] == "call":
                    branch = "callif"
                elif args[1] == "jumpstd":
                    branch = "jumpstdif"
                elif args[1] == "callstd":
                    branch = "callstdif"
                else:
                    error = ("ERROR: Command in 'if' must be jump, call, "
                             "jumpstd or callstd.")
                    raise Exception(error)
                operator = args[0]
                if operator in OPERATORS:
                    operator = OPERATORS[operator]
                words = [branch, operator, args[2]]
                parsed_list[org_i].append(words)

            else:
                parsed_list[org_i].append(words)
        if command != "=" and command != "if":
            for i, arg in enumerate(args):
                if arg[0] in (":", "@"):
                    continue
                arg_len = cmd_table[command]["args"][1][i]
                if arg[:2] == "0x":
                    this_arg_len = len(arg[2:]) // 2
                else:
                    this_arg_len = len(arg) // 2
                if this_arg_len > arg_len:
                    debug("We wan't this:", arg_len)
                    debug("But we have this:", this_arg_len)
                    debug("and the arg is this: ", arg)
                    error = ("ERROR: Arg too long (" + str(arg_len) + ", " +
                             str(this_arg_len) + ") on line " + str(num + 1))
                    raise Exception(error)
    return parsed_list, dyn

def text_len(text):
    #"0-9": "6",
    #"..": "6",
    #"A-Z": "6",
    #"a-h": "6",
    #"s-z": "6",
    #"m-q": "6",
    #"€": "6",
    #'"': "6 (both)",
    #"k": "6",
    #"/": "6",
    #"male": "6",
    #"female": "6",
    kernings = {
        "!": 3,
        "?": 6,
        ".": 3,
        #":": 5,
        "·": 3,
        "'": 3,
        ",": 3,
        "i": 4,
        "j": 5,
        "l": 3,
        "r": 5,
        ":": 3,
        "↑": 7,
        "→": 7,
        "↓": 7,
        "←": 7,
        "+": 7,
        " ": 3,
    }
    return sum([kernings[c] if c in kernings else 6 for c in text])

def autocut_text(text):
    maxlen = 35 * 6
    words = text.split(" ")
    text = ''
    line = ''
    i = 0
    delims = ('\\n', '\\p')
    delim = 0
    while i < len(words):
        word = words[i]
        if text_len(word) > maxlen:
            line += words[i] + " "
            i += 1
        while i < len(words) and text_len(line+words[i]) < maxlen:
            word = words[i]
            line += word + " "
            i += 1
        text += line.rstrip(" ") + delims[delim]
        delim = not delim
        line = ''
    text = text.rstrip('\\p').rstrip('\\n').rstrip(" ")
    return text

def find_nth(text, string, n):
    start = text.find(string)
    while start >= 0 and n > 1:
        start = text.find(string, start+len(string))
        n -= 1
    return start

def make_bytecode(script_list, cmd_table=pk.pkcommands):
    ''' Compile parsed script list '''
    hex_scripts = []
    for script in script_list:
        addr = script[0]
        bytecode = b""
        labels = []

        for line in script[1:]:
            command = line[0]
            args = line[1:]
            if command == '=':
                text = args[1:]
                bytecode += text_translate.ascii_to_hex(text)
            elif command == '#raw':
                hexcommand = args[0]
                bytecode += int(hexcommand, 16).to_bytes(1, "little")
            elif command[0] == ":":
                labels.append([command, len(bytecode)])
            else:
                hexcommand = cmd_table[command]["hex"]
                hexargs = bytearray()
                for i, arg in enumerate(args):
                    if arg[0] != "@" and arg[0] != ":":
                        arg_len = cmd_table[command]["args"][1][i]
                        if arg[0:2] != "0x":
                            arg = (int(arg) & 0xffffff)
                        else:
                            arg = int(arg, 16)
                        if "offset" in cmd_table[command]:
                            for o in cmd_table[command]["offset"]:
                                if o[0] == i:
                                    arg |= 0x8000000
                        try:
                            arg_bytes = arg.to_bytes(arg_len, "little")
                        except OverflowError:
                            debug(script)
                            debug(line)
                            error = ("Arg too long! "
                                     "We did something wrong preparsing... "
                                     "Arg: " + hex(arg) +
                                     "\nCommand: " + command)
                            raise Exception(error)
                        if len(cmd_table[command]["args"]) == 3:
                            arg_bytes = (cmd_table[command]["args"][2] +
                                         arg_bytes)
                        hexargs += arg_bytes
                    else:
                        if arg[0] == "@" and not USING_DYNAMIC:
                            error = "No #dynamic statement"
                            raise Exception(error)
                        # If we still have dynamic addresses, this compilation
                        # is just for calculating space,
                        # so we fill this with 00
                        arg = b"\x00\x00\x00\x08" # Dummy bytes, so we can
                                                  # size and then replace
                        if len(cmd_table[command]["args"]) == 3:
                            arg = (cmd_table[command]["args"][2] + arg)
                        hexargs += arg
                bytecode += hexcommand.to_bytes(1, "little") + hexargs

        hex_script = [addr, bytecode, labels]
        hex_scripts.append(hex_script)
    return hex_scripts


def put_addresses_labels(hex_chunks, text_script):
    ''' Calculates the real address for :labels and does the needed
        searches and replacements. '''
    for chunk in hex_chunks:
        for label in chunk[2]:
            vdebug(label)
            name = label[0]
            pos = hex(int(chunk[0], 16) + label[1])
            vdebug(pos)
            text_script = text_script.replace(" " + name + " ",
                                              " " + pos + " ")
            text_script = text_script.replace(" " + name + "\n",
                                              " " + pos + "\n")
            text_script = text_script.replace("\n" + name + "\n", "\n")
            text_script = text_script.replace("\n" + name + " ", "\n")
    return text_script


def put_addresses(hex_chunks, text_script, file_name, dyn):
    ''' Find free space and replace #dynamic @labels with real addresses '''
    dynamic_start = int(dyn, 16)
    rom_file_r = open(file_name, "rb")
    rom_bytes = rom_file_r.read()
    rom_file_r.close()
    offsets_found_log = ''
    last = dynamic_start
    for i, chunk in enumerate(hex_chunks):
        vdebug(chunk)
        offset = chunk[0]
        part = chunk[1] # The hex chunk we have to put somewhere
        #labels = chunk[2]
        if offset[0] != "@":
            continue
        length = len(part) + 2
        free_space = b"\xFF" * length
        address_with_free_space = rom_bytes.find(free_space, last)
        # It's always good to leave some margin around things.
        # If there is free space at the address the user has given us,
        # though, it's ok to use it without margin.
        if address_with_free_space != dynamic_start:
            address_with_free_space += 2
        if address_with_free_space == -1:
            print(len(rom_bytes))
            print(len(free_space))
            print(dynamic_start)
            print(last)
            raise Exception("No free space to put script.")
        text_script = text_script.replace(" " + offset + " ",
                                          " " + hex(address_with_free_space) + " ")
        text_script = text_script.replace(" " + offset + "\n",
                                          " " + hex(address_with_free_space) + "\n")
        hex_chunks[i][0] = hex(address_with_free_space)
        last = address_with_free_space + length + 10
        offsets_found_log += (offset + ' - ' +
                              hex(address_with_free_space) + '\n')
    return text_script, offsets_found_log

def write_hex_script(hex_scripts, rom_file_name):
    ''' Write every chunk of bytes onto the big ROM file '''
    file_name = rom_file_name
    for script in hex_scripts:
        with open(file_name, "rb") as f:
            rom_bytes = f.read()
        rom_ba = bytearray(rom_bytes)
        offset = int(script[0], 16)
        offset = get_rom_offset(offset)
        hex_script = script[1]
        vdebug("chunk length = " + hex(len(hex_script)))
        rom_ba[offset:offset+len(hex_script)] = hex_script

        with open(file_name, "wb") as f:
            f.write(rom_ba)


def decompile(file_name, offset, type_="script", raw=False,
              end_commands=END_COMMANDS, end_hex_commands=END_HEX_COMMANDS,
              cmd_table=pk.pkcommands, dec_table=pk.dec_pkcommands,
              verbose=0):
    # Preparem ROM text
    debug("'file name = " + file_name)
    debug("'address = " + hex(offset))
    debug("'---\n")
    with open(file_name, "rb") as f:
        rombytes = f.read()
    offsets = [[offset, type_]]
    textscript = ''
    decompiled_offsets = []
    while offsets:
        offset = offsets[0][0]
        type_ = offsets[0][1]
        if type_ == "script":
            textscript_, new_offsets = demake_bytecode(rombytes, offset,
                                                       offsets,
                                                       end_commands=end_commands,
                                                       end_hex_commands=end_hex_commands,
                                                       raw=raw,
                                                       cmd_table=cmd_table,
                                                       dec_table=dec_table,
                                                       verbose=verbose)
            textscript += ("#org " + hex(offset) + "\n" +
                           textscript_ + "\n")
            for new_offset in new_offsets:
                new_offset[0] &= 0xffffff
                if (new_offset not in offsets and
                        new_offset[0] not in decompiled_offsets):
                    offsets += [new_offset]
        if type_ == "text":
            text = decompile_text(rombytes, offset, raw=raw)
            lines = [text[i:i+80] for i in range(0, len(text), 80)]
            text = "".join([("= " + line + "\n") for line in lines])
            textscript += ("#org " + hex(offset) + "\n" + text)
        # TODO: make them separate, nicer mov decomp
        if type_ == "movs" or type_ == "raw":
            textscript_tmp = decompile_movs(rombytes, offset, raw=raw)
            textscript += ("#org " + hex(offset) + "\n" +
                           textscript_tmp + "\n")
        del offsets[0]
        decompiled_offsets.append(offset)
        # Removing duplicates doesn't hurt, right?
        decompiled_offsets = list(set(decompiled_offsets))
    return textscript


def get_rom_offset(offset):
    rom_offset = offset
    if rom_offset >= 0x8000000:
        rom_offset -= 0x8000000
    return rom_offset

def demake_bytecode(rombytes, offset, added_offsets,
                    end_commands=END_COMMANDS,
                    end_hex_commands=END_HEX_COMMANDS, raw=False,
                    cmd_table=pk.pkcommands,
                    dec_table=pk.dec_pkcommands,
                    verbose=0):
    rom_offset = get_rom_offset(offset)
    offsets = []
    hexscript = rombytes
    i = rom_offset
    textscript = ""
    text_command = ""
    hex_command = 0
    hex_command = hexscript[i]
    nop_count = 0 # Stop on 10 nops for safety
    while (text_command not in end_commands and
           hex_command not in end_hex_commands):
        hex_command = hexscript[i]
        orig_i = i
        if hex_command in dec_table and not raw:
            text_command = dec_table[hex_command]
            textscript += text_command
            i += 1
            command_data = cmd_table[text_command]
            if "args" in command_data:
                if len(command_data["args"]) == 3:
                    i += len(command_data["args"][2])
                for n, arg_len in enumerate(command_data["args"][1]):
                    arg = hexscript[i:i + arg_len]
                    arg = int.from_bytes(arg, "little")
                    if "offset" in command_data:
                        for o_arg_n, o_type in command_data["offset"]:
                            if o_arg_n == n:
                                tuple_to_add = [arg, o_type]
                                if tuple_to_add not in added_offsets+offsets:
                                    offsets.append(tuple_to_add)
                    textscript += " " + hex(arg)
                    i += arg_len
        else:
            textscript += "#raw " + hex(hex_command)
            i += 1
        if hex_command == 0:
            nop_count += 1
            if nop_count >= MAX_NOPS and MAX_NOPS != 0:
                textscript += " ' Too many nops. Stopping"
                break
        else:
            nop_count = 0

        if verbose >= 1:
            textscript += " //" + " ".join(hex(n)[2:].zfill(2) for n in hexscript[orig_i:i])
            if verbose >= 2:
                textscript += " -  " + hex(orig_i)
        textscript += "\n"

    #print(textscript)
    #print("offsets")
    #for o, type in offsets:
    #    print(hex(o), type)
    #print(offsets)

    return textscript, offsets

def decompile_rawh(romtext, offset, end_hex_commands=[0xFF], raw=False):
    rom_offset = get_rom_offset(offset)
    vdebug(offset)
    hexscript = romtext
    i = rom_offset
    textscript = ""
    hex_command = ""
    while hex_command not in end_hex_commands:
        try:
            hex_command = hexscript[i]
        except IndexError:
            break
        textscript += "#raw " + hex(hex_command)
        i += 1
        textscript += "\n"
    return textscript

def decompile_rawb(romtext, offset, end_hex_commands=[0xFF], raw=False):
    rom_offset = get_rom_offset(offset)
    vdebug(offset)
    hexscript = romtext
    i = rom_offset
    textscript = ""
    hex_command = ""
    while hex_command not in end_hex_commands:
        try:
            hex_command = hexscript[i]
        except IndexError:
            break
        textscript += "#raw " + hex(hex_command)
        i += 1
        textscript += "\n"
    return textscript

# TODO: use nice define'd thingies
def decompile_movs(romtext, offset, end_hex_commands=[0xFE, 0xFF], raw=False):
    rom_offset = get_rom_offset(offset)
    vdebug(offset)
    hexscript = romtext
    i = rom_offset
    textscript = ""
    hex_command = ""
    while hex_command not in end_hex_commands:
        try:
            hex_command = hexscript[i]
        except IndexError:
            break
        textscript += "#raw " + hex(hex_command)
        i += 1
        textscript += "\n"
    return textscript


def decompile_text(romtext, offset, raw=False):
    rom_offset = get_rom_offset(offset)
    start = rom_offset
    end = start + romtext[start:].find(b"\xff")
    text = romtext[start:end]
    text_table = text_translate.table_str
    # decoding table
    d_table = text_translate.read_table_decode(text_table)
    translated_text = text_translate.hex_to_ascii(text, d_table)
    return translated_text


def write_text_script(text, file_name):
    if USING_WINDOWS:
        text = text.replace("\n", "\r\n")
    with open(file_name, "w") as script_file:
        script_file.write(text)


def open_script(file_name):
    ''' Open file and replace \\r\\n with \\n '''
    with open(file_name, "r") as script_file:
        script_text = script_file.read()
    script_text = script_text.replace("\r\n", "\n")
    return script_text

def assemble(script, rom_file_name, cmd_table=pk.pkcommands):
    ''' Compiles a plain script and returns a tuple containing
        a list and a string. The string is the #dyn log.
        The list contains a list for every location where
        something should be written. These lists are 2
        elements each, the offset where data should be
        written and the data itself '''
    debug("parsing...")
    parsed_script, dyn = asm_parse(script, cmd_table=cmd_table)
    vpdebug(parsed_script)
    debug("compiling...")
    hex_script = make_bytecode(parsed_script, cmd_table=cmd_table)
    debug(hex_script)
    log = ''
    debug("doing dynamic and label things...")

    if dyn[0] and rom_file_name:
        debug("going dynamic!")
        debug("replacing dyn addresses by offsets...")
        script, log = put_addresses(hex_script, script,
                                    rom_file_name, dyn[1])
        vdebug(script)

    # Now with :labels we have to recompile even if

    debug("re-preparsing")

    script = put_addresses_labels(hex_script, script)
    vdebug(script)

    parsed_script, dyn = asm_parse(script, cmd_table=cmd_table)
    debug("recompiling")
    hex_script = make_bytecode(parsed_script, cmd_table=cmd_table)
    debug("yay!")

    # Remove the labels list, which will be empty and useless now
    for chunk in hex_script:
        del chunk[2] # Will always be []
    return hex_script, log

def get_base_directive(rom_fn):
    with open(rom_fn, "rb") as f:
        f.seek(0xAC)
        code = f.read(4)
    return "#define " + {
        b"AXVE": "RS",
        b"BPRE": "FR",
        b"BPEE": "EM"}[code] + "\n"

def nice_dbg_output(hex_scripts):
    text = ''
    for offset, hex_script in hex_scripts:
        script_text = offset + '\n'
        line = ''
        for byte in hex_script:
            line += '%02x ' % byte
            if len(line) > 40:
                script_text += line + '\n'
                line = ''
        script_text += line
        script_text += '\n'
        text += script_text
    return text

def get_canvas():
    with open(os.path.join(data_path, "canvas.pks"), encoding="utf8") as f:
        text = f.read()
    return text

def make_clean_script(hex_script):
    text = "// cleaning script"
    for addr, chunk in hex_script:
        text += "\n#org {}\n".format(addr)
        for _ in range(len(chunk)):
            text += "#raw 0xFF\n"
        #text += "="+"\\xFF"*len(chunk)+"\n"
    return text

def get_program_dir():
    try:
        return os.path.dirname(__file__)
    except NameError:
        return os.path.dirname(sys.executable)

def main():
    description = 'Red Alien, an Advanced (Pokémon) Script Compiler'
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument('--quiet', action='store_true', help='Be quiet')
    parser.add_argument('--verbose', '-v', action='count', help='Be verbose. Like, a lot')
    parser.add_argument('--mode', default="event", type=str,
            help='what kind of bytecode, default is map events (event)')
    subparsers = parser.add_subparsers(help='available commands:')

    parser_c = subparsers.add_parser('c', help='compile')
    parser_c.add_argument('rom', help='path to ROM image')
    parser_c.add_argument('script', help='path to pokemon script')
    parser_c.add_argument('--clean', action='store_true',
                          help='Produce a cleaning script')
    parser_c.set_defaults(command='c')

    parser_b = subparsers.add_parser('b', help='debug')
    parser_b.add_argument('rom', help='path to ROM image')
    parser_b.add_argument('script', help='path to pokemon script')
    parser_b.add_argument('--compile-only', action='store_true',
                          help='Compile only, don\'t parse the ASM')
    parser_b.add_argument('--parse-only', action='store_true',
                          help='Parse only, don\'t assemble')
    parser_b.add_argument('--clean', action='store_true',
                          help='Produce a cleaning script')
    parser_b.set_defaults(command='b')

    parser_d = subparsers.add_parser('d', help='decompile')
    parser_d.add_argument('rom', help='path to ROM image')
    parser_d.add_argument('offset', help='where to decompile')
    parser_d.add_argument('--raw', action='store_true',
                          help='Be dumb (display everything as raw bytes)')
    parser_d.add_argument('--text', action='store_true',
                          help='Decompile as text')
    h = 'How many nop bytes until it stops (0 to never stop). Defaults to 10'
    parser_d.add_argument('--max-nops', default=10, type=int, help=h)

    for end_command in END_COMMANDS:
        msg = ('whether or not to stop decompiling when a ' + end_command +
               ' is found')
        parser_d.add_argument('--continue-on-' + end_command,
                              action='append_const',
                              dest='END_COMMANDS_to_delete',
                              const=end_command, help=msg)
    h = 'whether or not to stop decompiling when a 0xFF byte is found'
    parser_d.add_argument('--continue-on-0xFF', action='store_true', help=h)
    parser_d.set_defaults(command='d')

    args = parser.parse_args()
    modes = {
            "event": (pk.pkcommands, pk.dec_pkcommands, pk.end_pkcommands),
            #"battle": ,
            "battle_ai": (pk.aicommands, pk.dec_aicommands, pk.end_aicommands),
            }
    if "command" not in args or args.mode not in modes:
        parser.print_help()
        sys.exit(1)
    cmd_table, dec_table, end_cmds = modes[args.mode]

    global QUIET, MAX_NOPS, VERBOSE
    QUIET = args.quiet
    VERBOSE = args.verbose
    MAX_NOPS = (args.MAX_NOPS if MAX_NOPS in args else 10)

    if args.command in ["b", "c"]:
        debug("reading file...", args.script)
        script = open_script(args.script)
        vdebug(script)
        debug("compiling high-level stuff...")
        try:
            script = get_base_directive(args.rom) + script
        except KeyError:
            pass
        include_path = (".", os.path.dirname(args.rom),
                        os.path.dirname(args.script), get_program_dir(),
                        data_path)
        script = dirty_compile(script, include_path)
        vdebug(script)
        if args.command == "b" and args.compile_only:
            print(script)
            return
        elif args.command == "b" and args.parse_only:
            parsed_script, dyn = asm_parse(script, cmd_table=cmd_table)
            pprint(parsed_script)
            print(dyn)
            return
        hex_script, log = assemble(script, args.rom, cmd_table=cmd_table)
        if args.clean:
            with open(args.script+".clean.pks", "w") as f:
                f.write(make_clean_script(hex_script))

        if args.command == "c":
            write_hex_script(hex_script, args.rom)
        else:
            debug("\nHex:")
            for addr, chunk in hex_script:
                debug(addr)
                phdebug(chunk)
        print("\nLog:")
        print(log)

    elif args.command == "d":
        if not args.END_COMMANDS_to_delete:
            args.END_COMMANDS_to_delete = []
        for end_command in args.END_COMMANDS_to_delete:
            END_COMMANDS.remove(end_command)
        print("'" + '-'*20)
        end_hex_commands = [] if args.continue_on_0xFF else END_HEX_COMMANDS
        type_ = "text" if args.text else "script"
        print(end_cmds)
        print(decompile(args.rom, int(args.offset, 16), type_, raw=args.raw,
                        end_hex_commands=end_hex_commands,
                        cmd_table=cmd_table,
                        dec_table=dec_table,
                        end_commands=end_cmds,
                        verbose=args.verbose if args.verbose is not None else 0))


if __name__ == "__main__":
    main()



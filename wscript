#! /usr/bin/env python
# encoding: utf-8

import platform, sys
from waflib.Tools import d_scan

## BUILD DESCRIPTION SECTION ##

VERSION='0.0.1'
APPNAME='xomb'

top = '.'
out = '_build_'

supported_architectures = ['x86_64', 'x86'] ## Will default to first architecture specified in this list if no supported architecture is detected or specified with --arch.

def options(opt):
	opt.load('compiler_d')
	opt.load('compiler_c')
	opt.load('nasm')
	opt.add_option('--arch', action='store', default=False, help='Target architecture')

def configure(conf):
	conf.load('compiler_c')
	conf.load('compiler_d')
	conf.load('nasm')
	conf.find_program('ld', var='LINK_LD')
	conf.find_program('mkisofs', var='MKISOFS')
	#conf.env.LIB_PTHREAD = ['pthread'] ## Probably we are never going to need this.
	
	# Figure out what Architecture we want to compile for
	detected_architecture = platform.machine()
	if conf.options.arch in supported_architectures:
		conf.env.ARCHITECTURE = [conf.options.arch]
		print('→ --arch specified, overriding detected architecture, %r' % detected_architecture)
	elif detected_architecture in supported_architectures:
		conf.env.ARCHITECTURE = [detected_architecture]
	else:
		print('→ Detected architecture %r not supported.  Defaulting to %r.' % (detected_architecture, supported_architectures[0]))
		print('→ Architecture must be one of %r' % supported_architectures)
		conf.env.ARCHITECTURE = [supported_architectures[0]]
	print('→ ARCHITECTURE is %r' % conf.env.ARCHITECTURE)
	# TODO: something productive with this information now that we have it
	
	# Set up the standard flags
	conf.env.DEFAULT_DFLAGS = ['-nodefaultlib', '-g']
	# Set up architecture-dependent flags
	# NOTE: wilkie says these flags are not used when compiling app foo -- only the kernel
	if 'x86_64' in conf.env.ARCHITECTURE:
		conf.env.ARCH_DFLAGS = ['-mattr=-sse', '-m64', '-code-model=large']
		# Set up the assembly flags
		conf.env.ASFLAGS = ['-felf64']
	else: # Put x86 arch stuff somewhere, someday
		conf.env.ARCH_DFLAGS = []
		# Set up the assembly flags
		conf.env.ASFLAGS = []
	
	# Copy nativecall.d from the proper architecture to build/user/ in a portable way
	print('Obtaining %r nativecall.d' % conf.env.ARCHITECTURE[0])
	path_to_nativecall_d = 'kernel/arch/' + conf.env.ARCHITECTURE[0] + '/imports/nativecall.d'
	orig = conf.path.find_node(path_to_nativecall_d)
	copy_text = orig.read()
	
	dest = conf.bldnode.make_node('user/nativecall.d')
	dest.parent.mkdir()
	dest.write(copy_text)
	
	conf.env.append_value('cfg_files', dest.abspath())
	
	# Copy bochsrc to out
	orig = conf.path.find_node('build/bochsrc')
	copy_text = orig.read()
	dest = conf.bldnode.make_node('bochsrc')
	dest.write(copy_text)
	conf.env.append_value('cfg_files', dest.abspath())

def build(bld):
	# Build mindrt and drt0
	bld.recurse('runtimes/mindrt')
	
	# Build xsh
	#bld.recurse('app/d/xsh') ## This would also be possible
	xsh_includes = ['app/d/xsh', '.', 'runtimes', 'runtimes/mindrt']
	xsh_include_resources = []
	for include in xsh_includes:
		xsh_include_resources.append(bld.path.find_dir(include))
	dep_scanner = d_scan.d_parser(bld, xsh_include_resources)
	xsh_sources = [bld.path.find_resource('app/d/xsh/xsh.d')]
	dep_scanner.start(xsh_sources[0])
	xsh_sources += dep_scanner.nodes
	xsh = bld(features     = 'd ld_link',
		source   = xsh_sources,
		use      = 'drt0',
		includes = xsh_include_resources,
		name     = 'xsh',
		target   = 'iso/binaries/xsh')
	xsh.env.DFLAGS = ['-nodefaultlib']
	xsh.env.LINKFLAGS = ['-nostdlib', '-nodefaultlibs']

	# Build xomb kernel
	# This is similar to the way dsss specifies the sources to use for the xomb target.
	xomb_includes = ['.', 'kernel/runtime', 'kernel/arch/' + bld.env.ARCHITECTURE[0], 'kernel/core']
	xomb_include_resources = []
	for include in xomb_includes:
		xomb_include_resources.append(bld.path.find_dir(include))
	dep_scanner = d_scan.d_parser(bld, xomb_include_resources)
	kmain_source = bld.path.find_resource('kernel/core/kmain.d')
	xomb_sources = [kmain_source]
	# These three source files get pulled in by ldc -deps, but not by waf's dependency scanner for some reason
	extra_sources = ['kernel/runtime/object.d', 'kernel/runtime/util.d', 'kernel/runtime/std/moduleinit.d']
	# These source files are explicitly mentioned in x86_64.conf, before relying on dsss
	extra_sources += ['kernel/runtime/invariant.d', 'kernel/runtime/dstubs.d']
	# Let's put them into our list of sources to be included in the xomb kernel
	for s in extra_sources:
		xomb_sources.append(bld.path.find_resource(s))
	# These source files are also explicitly mentioned in x86_64.conf
	xomb_sources += bld.path.ant_glob('kernel/arch/' + bld.env.ARCHITECTURE[0] + '/boot/*.s')
	xomb_sources += bld.path.ant_glob('kernel/runtime/std/typeinfo/*.d')
	# Scan kmain.d's dependencies...
	dep_scanner.start(kmain_source)
	# And include all of them in the list of sources to be compiled into the xomb kernel
	xomb_sources.extend(dep_scanner.nodes)
	# Register the xomb build task
	# No way can we use ldc to link at this point.  It has -lpthread, -ldl, and -lm COMPILED IN for when
	# it passes arguments to the linker.  So we have to use ld directly.
	xomb = bld(features     = 'd ld_link linkld_gen',
		source   = xomb_sources,
		includes = xomb_include_resources,
		use      = 'mindrt',
		name     = 'xomb',
		target   = 'iso/boot/xomb')
	# Set up compile and link flags
	xomb.env.DFLAGS = bld.env.DEFAULT_DFLAGS + bld.env.ARCH_DFLAGS
	# TODO: Make this reflect the target architecture
	xomb.env.LD_PRESOURCE_FLAGS = ['-belf64-x86-64']
	xomb.env.LINKFLAGS = ['-nostdlib', '-nodefaultlibs']

	# Now, to make the iso we need to get grub into _build_/iso/boot
	# This is better than the nativecall.d stuff, but still a bit hacky -- improvements welcome
	import shutil, os
	grub_source = bld.path.find_node('build/iso/boot/grub')
	grub_dest = bld.bldnode.make_node('iso/boot/grub')
	if not os.path.exists(grub_dest.abspath()):
		#grub_dest.parent.mkdir() ## Unnecessary because copytree does this for us.
		shutil.copytree(grub_source.abspath(), grub_dest.abspath())
	
	# Now we can make a build task for mkisofs
	iso = bld(source = bld.bldnode.find_node('iso').ant_glob('*'),
		target = 'xomb.iso',
		rule   = '${MKISOFS} -R -b  boot/grub/stage2_eltorito -no-emul-boot -boot-load-size 16 -boot-info-table -input-charset UTF-8 -o ${TGT} ' + bld.bldnode.find_node('iso').abspath(),
		use    = 'xomb xsh',
		after  = 'ld_link',)

## TOOLS SECTION ##

from waflib.Task import Task
from waflib.TaskGen import before, feature, extension, after
from waflib.Tools import ccroot
import re
from waflib import Logs

class ld_link(ccroot.link_task):
	run_str = '${LINK_LD} ${LD_PRESOURCE_FLAGS} ${SRC} ${RPATH_ST:RPATH} ${FRAMEWORKPATH_ST:FRAMEWORKPATH} ${FRAMEWORK_ST:FRAMEWORK} ${STLIBPATH_ST:STLIBPATH} ${STLIB_ST:STLIB} ${LIBPATH_ST:LIBPATH} ${LIB_ST:LIB} ${LINKFLAGS} -o ${TGT}'

linker_ld_template = '''/*
 * linker.ld
 *
 *  This script is given as the only script to the linker
 *  Will map boot.S to LMA, and then everything else
 *  will be linked to the VMA and mapped at the LMA
 *  _etext, _edata, _end are defined here
 *
 */

/*
 *  KERNEL LINK LOCATIONS
 *
 *  these are the locations to map to
 *  they need to be set within boot.h
 *  as well
 *
 */

kernel_VMA = 0xffff800000000000;
kernel_LMA = 0x100000;

/* start from the entry point */
ENTRY(_start)
SECTIONS
{
	/* link from LMA */
	. = kernel_LMA;

	_kernelLMA = .;

	_boot = .;

	/* boot.S is ran in linear addresses */
	.text_boot :
	{
		/*%boot_o%*/ (.text)
	}

	_eboot = .;

	.text_trampoline ALIGN(0x1000) :
	{
		PROVIDE(_trampoline = .);

		/*%trampoline_o%*/ (.text)
	}

	PROVIDE(_etrampoline = .);

	/* link from VMA */
	. = . + kernel_VMA;

	_text = .;

	_kernel = .;
	_kernelVMA = kernel_VMA;

	/* the rest of the code links to higher memory */
	.text : AT(ADDR(.text) - kernel_VMA + kernel_LMA)
	{
		code = .;
		*(.text)
		*(.text*)

		/* read only data */
		*(.rodata*)
		*(.rdata*)

		. = ALIGN(4096);
	}

	/*PROVIDE(_ekernel = .);*/

	/* _etext defined */
	_etext = .; PROVIDE(etext = .);

	/* data section */
	.data : AT(ADDR(.data) - kernel_VMA + kernel_LMA)
	{
		_data = .;

		data = .;
		*(.data)

		/* constructors and deconstructors
		(if needed, doesn't hurt) */

		start_ctors = .;
		*(.ctor*)
		end_ctors = .;

		start_dtors = .;
		*(.dtor*)
		end_dtors = .;

		. = ALIGN(4096);
	}

	/* _edata defined */
	_edata = .; PROVIDE (edata = .);


	/* static code */
	.bss : AT(ADDR(.bss) - kernel_VMA + kernel_LMA)
	{
		_bss = .;
		sbss = .;
		*(.bss)
		. = ALIGN(4096);
	}

	_ebss = .;
	ebss = .;

	/*  */
	.ehframe : AT(ADDR(.ehframe) - kernel_VMA + kernel_LMA)
	{
		ehframe = .;
		*(.ehframe)
		. = ALIGN(4096);
	}


	/* _end defined (for posterity and tradition) */
	_end = .; PROVIDE (end = .);

	_ekernel = .;
}'''

class generate_linker_script(Task):
	def run(self):
		boot_re = re.compile(r'/\*%boot_o%\*/')
		trampoline_re = re.compile(r'/\*%trampoline_o%\*/')
		linker_ld = re.sub(boot_re, self.extra_information['boot.s'].bldpath(), linker_ld_template)
		linker_ld = re.sub(trampoline_re, self.extra_information['trampoline.s'].bldpath(), linker_ld)
		self.outputs[0].write(linker_ld)

@feature('linkld_gen')
@after('process_source', 'apply_link')
@before('process_use')
def do_script_gen(self):
	ins = list()
	extra_information = dict() #[x.outputs[0] for x in self.compiled_tasks]
	for x in self.compiled_tasks:
		if x.inputs[0].name == 'boot.s':
			ins.append(x.outputs[0])
			extra_information['boot.s'] = x.outputs[0]
		if x.inputs[0].name == 'trampoline.s':
			ins.append(x.outputs[0])
			extra_information['trampoline.s'] = x.outputs[0]
	tsk = self.create_task('generate_linker_script', ins, self.path.find_or_declare('linker.ld'))
	tsk.extra_information = extra_information
	if getattr(self, 'link_task', None):
		self.link_task.set_run_after(tsk)
		self.link_task.dep_nodes = [tsk.outputs[0]]
		self.link_task.env.append_value('LINKFLAGS', ['-T' + tsk.outputs[0].bldpath()])
	else:
		Logs.warn("Not much point in using linkld_gen if you're not linking.")
		Logs.warn("Unable to add -T" + tsk.outputs[0].bldpath() + " to LINKFLAGS.  Probably the script won't get used.")



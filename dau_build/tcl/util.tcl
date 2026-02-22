#
# dau-build TCL utility library
#
# Reusable procedures for Vivado FPGA build flows.
# Source this file from project-specific TCL scripts:
#
#   source /path/to/dau-build/dau_build/tcl/util.tcl
#

namespace eval ::dau {

    # ----------------------------------------------------------------
    # Logging
    # ----------------------------------------------------------------
    proc log {level msg} {
        set ts [clock format [clock seconds] -format "%Y-%m-%d %H:%M:%S"]
        puts "\[$ts\] \[$level\] $msg"
    }

    proc info {msg}  { log "INFO"  $msg }
    proc warn {msg}  { log "WARN"  $msg }
    proc error {msg} { log "ERROR" $msg }

    # ----------------------------------------------------------------
    # Project helpers
    # ----------------------------------------------------------------
    proc open_or_create_project {project_name part_name {project_dir "."}} {
        # Open existing project or create a new one
        set xpr "${project_dir}/${project_name}.xpr"
        if {[file exists $xpr]} {
            ::dau::info "Opening existing project: $xpr"
            open_project $xpr
        } else {
            ::dau::info "Creating new project: $project_name (part: $part_name)"
            create_project $project_name $project_dir -part $part_name -force
        }
    }

    proc add_rtl_sources {filelist {fileset sources_1}} {
        # Add a list of RTL source files to the project
        ::dau::info "Adding [llength $filelist] source file(s)"
        add_files -fileset $fileset -norecurse $filelist
        update_compile_order -fileset $fileset
    }

    proc add_constraints {filelist {fileset constrs_1}} {
        # Add constraint files to the project
        ::dau::info "Adding [llength $filelist] constraint file(s)"
        add_files -fileset $fileset -norecurse $filelist
    }

    # ----------------------------------------------------------------
    # Synthesis
    # ----------------------------------------------------------------
    proc run_synthesis {{jobs 4} {run_name synth_1}} {
        ::dau::info "Running synthesis ($run_name, $jobs jobs) ..."
        reset_run $run_name
        launch_runs $run_name -jobs $jobs
        wait_on_run $run_name
        set status [get_property STATUS [get_runs $run_name]]
        ::dau::info "Synthesis status: $status"
        if {[string match "*ERROR*" $status]} {
            ::dau::error "Synthesis failed!"
            return -code error "Synthesis failed"
        }
        return $status
    }

    # ----------------------------------------------------------------
    # Implementation
    # ----------------------------------------------------------------
    proc run_implementation {{jobs 4} {run_name impl_1} {to_step write_bitstream}} {
        ::dau::info "Running implementation ($run_name, $jobs jobs, to_step=$to_step) ..."
        launch_runs $run_name -jobs $jobs -to_step $to_step
        wait_on_run $run_name
        set status [get_property STATUS [get_runs $run_name]]
        ::dau::info "Implementation status: $status"
        if {[string match "*ERROR*" $status]} {
            ::dau::error "Implementation failed!"
            return -code error "Implementation failed"
        }
        return $status
    }

    # ----------------------------------------------------------------
    # Bitstream / flash image generation
    # ----------------------------------------------------------------
    proc generate_flash_images {bitstream_path output_dir {flash_size 16} {flash_iface SPIx4}} {
        # Generate MCS and BIN flash images from a bitstream
        ::dau::info "Generating flash images from $bitstream_path"
        file mkdir $output_dir

        set mcs_path "${output_dir}/top.mcs"
        set bin_path "${output_dir}/top.bin"

        write_cfgmem -format mcs -size $flash_size -interface $flash_iface \
            -force -loadbit "up 0 $bitstream_path" -file $mcs_path

        write_cfgmem -format bin -size $flash_size -interface $flash_iface \
            -force -loadbit "up 0 $bitstream_path" -file $bin_path

        ::dau::info "Flash images written to $output_dir"
        return [list $mcs_path $bin_path]
    }

    # ----------------------------------------------------------------
    # Programming
    # ----------------------------------------------------------------
    proc program_device {bitstream_path {target_device "xc7a200t_0"} {hw_server "localhost:3121"}} {
        # Program FPGA via JTAG
        ::dau::info "Programming device $target_device with $bitstream_path"

        open_hw_manager
        connect_hw_server -url $hw_server -allow_non_jtag
        open_hw_target

        set device [get_hw_devices $target_device]
        current_hw_device $device

        set_property PROGRAM.FILE $bitstream_path $device
        program_hw_devices $device

        ::dau::info "Device programmed successfully"
        close_hw_target
        close_hw_manager
    }

    proc flash_device {mcs_path {flash_part "s25fl256sxxxxxx0-spi-x1_x2_x4"} {target_device "xc7a200t_0"} {hw_server "localhost:3121"}} {
        # Flash program via JTAG + SPI
        ::dau::info "Flashing device with $mcs_path"

        open_hw_manager
        connect_hw_server -url $hw_server -allow_non_jtag
        open_hw_target

        set device [get_hw_devices $target_device]
        current_hw_device $device

        create_hw_cfgmem -hw_device $device -mem_dev [lindex [get_cfgmem_parts $flash_part] 0]
        set cfgmem [get_property PROGRAM.HW_CFGMEM $device]

        set_property PROGRAM.BLANK_CHECK  0 $cfgmem
        set_property PROGRAM.ERASE        1 $cfgmem
        set_property PROGRAM.CFG_PROGRAM  1 $cfgmem
        set_property PROGRAM.VERIFY       1 $cfgmem
        set_property PROGRAM.CHECKSUM     0 $cfgmem
        set_property PROGRAM.ADDRESS_RANGE {use_file} $cfgmem
        set_property PROGRAM.FILES [list $mcs_path] $cfgmem
        set_property PROGRAM.UNUSED_PIN_TERMINATION {pull-none} $cfgmem

        program_hw_cfgmem -hw_cfgmem $cfgmem

        ::dau::info "Flash programmed successfully"
        close_hw_target
        close_hw_manager
    }

    # ----------------------------------------------------------------
    # Simulation netlist
    # ----------------------------------------------------------------
    proc write_sim_netlist {output_path {mode funcsim}} {
        # Write Verilog simulation netlist
        ::dau::info "Writing $mode netlist to $output_path"
        write_verilog -mode $mode $output_path -force
    }

    # ----------------------------------------------------------------
    # Block design helpers
    # ----------------------------------------------------------------
    proc add_rtl_to_bd {module_name instance_name bd_name} {
        # Add an RTL module reference to a block design
        ::dau::info "Adding $module_name as $instance_name to block design $bd_name"
        open_bd_design [get_files ${bd_name}.bd]
        create_bd_cell -type module -reference $module_name $instance_name
    }

    proc connect_bd_clock_reset {instance_name clk_pin rst_pin} {
        # Connect standard clock and reset to a BD cell
        connect_bd_net [get_bd_pins $clk_pin] [get_bd_pins ${instance_name}/aclk]
        connect_bd_net [get_bd_pins $rst_pin] [get_bd_pins ${instance_name}/aresetn]
    }

    proc expand_interconnect_mi {ic_name} {
        # Add one more master interface to an AXI interconnect, return new port index
        set num_mi [get_property CONFIG.NUM_MI [get_bd_cells $ic_name]]
        set new_mi [expr {$num_mi + 1}]
        set new_idx [format "%02d" $num_mi]
        set_property CONFIG.NUM_MI $new_mi [get_bd_cells $ic_name]
        ::dau::info "Expanded $ic_name to $new_mi master interfaces (new: M${new_idx})"
        return $new_idx
    }

    proc expand_interconnect_si {ic_name} {
        # Add one more slave interface to an AXI interconnect, return new port index
        set num_si [get_property CONFIG.NUM_SI [get_bd_cells $ic_name]]
        set new_si [expr {$num_si + 1}]
        set new_idx [format "%02d" $num_si]
        set_property CONFIG.NUM_SI $new_si [get_bd_cells $ic_name]
        ::dau::info "Expanded $ic_name to $new_si slave interfaces (new: S${new_idx})"
        return $new_idx
    }

    # ----------------------------------------------------------------
    # Full build flow (convenience)
    # ----------------------------------------------------------------
    proc full_build {{jobs 4} {run_synth synth_1} {run_impl impl_1}} {
        ::dau::info "Starting full build flow ..."
        run_synthesis $jobs $run_synth
        open_run $run_synth
        run_implementation $jobs $run_impl
        ::dau::info "Full build complete."
    }

}

# Export namespace
namespace import ::dau::*

********************************************************************
* SysGo - Kickstart program module
*
* Edt/Rev  YYYY/MM/DD  Modified by
* Comment
* ------------------------------------------------------------------
*   1      2024/03/06  Boisy G. Pitre
* Forked from CoCo 3 specific port.
*
*   2      2025/08/23  Matt Massie
* Support Wildbits K,K2,Jr,Jr2 models.
*
*   3      2025/12/26  Matt Massie
* Forks scfg -dl to  load default palettes for models or reads
* sys/defaultsettings for foreground, background, screen size, font to load. 
*
*   4      2026/08/01  Codex
* Add optional, polled Jr2 system-UART boot diagnostics.
*
*   5      2026/08/01  Codex
* Preserve X across BOOTLOG so diagnostics do not corrupt caller state.

                    use       defsfile

                    ifndef    BOOT_DIAG
BOOT_DIAG           equ       0
                    endc
                    ifndef    BOOT_DIAG_BAUD
BOOT_DIAG_BAUD      equ       9600
                    endc
                    ifndef    RUN_STARTUP
RUN_STARTUP         equ       1
                    endc

BOOTLOG             macro
                    ifne      BOOT_DIAG
                    pshs      x
                    leax      >\1,pcr
                    lbsr      BootDiagWrite
                    puls      x
                    endc
                    endm

                    section   bss
InitAddr            rmb       2                    
                    rmb       100
                    endsect
                    
* Default process priority
DefPrior            set       128
                    
                    section   code
ExecDir             fcc       ".../CMDS"
                    fcb       C$CR

Shell               fcc       "Shell"
                    fcb       C$CR
AutoEx              fcc       "AutoEx"
                    fcb       C$CR
AutoExPr            fcc       ""
                    fcb       C$CR
AutoExPrL           equ       *-AutoExPr

Startup             fcc       "startup -p"
                    fcb       C$CR
StartupL            equ       *-Startup

InitScrn            fcc       "scfg"
                    fcb       C$CR
InitScrn2           fcc       "-dl"
                    fcb       C$CR
InitScrnL2          equ       *-InitScrn2

ShellPrm            equ       *
                    ifgt      Level-1
                    fcc       "i=/1"
                    endc
CRtn                fcb       C$CR
ShellPL             equ       *-ShellPrm

* Default time packet
* Set to 59 seconds so that at 00 seconds, the RTC (if any) can set the time.
* If no RTC is available, then the soft clock starts at January 1 of the new year.
DefTime             fcb       85,12,31,23,59,59

Init                fcs       /Init/

                    ifne      BOOT_DIAG
BootDiagClock       equ       25175000
BootDiagDivisor     equ       (BootDiagClock+(BOOT_DIAG_BAUD*8))/(BOOT_DIAG_BAUD*16)
Diag00              fcc       "SG00 ENTRY"
                    fcb       C$CR,C$LF,0
Diag01              fcc       "SG01 ICPT"
                    fcb       C$CR,C$LF,0
Diag02              fcc       "SG02 TIME"
                    fcb       C$CR,C$LF,0
Diag03              fcc       "SG03 INIT"
                    fcb       C$CR,C$LF,0
Diag04              fcc       "SG04 DATADIR"
                    fcb       C$CR,C$LF,0
Diag05              fcc       "SG05 EXECDIR"
                    fcb       C$CR,C$LF,0
Diag06              fcc       "SG06 SCFG FORK"
                    fcb       C$CR,C$LF,0
Diag07              fcc       "SG07 SCFG CHILD"
                    fcb       C$CR,C$LF,0
Diag08              fcc       "SG08 SCFG DONE"
                    fcb       C$CR,C$LF,0
Diag09              fcc       "SG09 BANNER"
                    fcb       C$CR,C$LF,0
Diag10
                    ifne      RUN_STARTUP
                    fcc       "SG10 STARTUP FORK"
                    else
                    fcc       "SG10 STARTUP SKIP"
                    endc
                    fcb       C$CR,C$LF,0
Diag11              fcc       "SG11 STARTUP CHILD"
                    fcb       C$CR,C$LF,0
Diag12              fcc       "SG12 STARTUP DONE"
                    fcb       C$CR,C$LF,0
Diag13              fcc       "SG13 AUTOEX FORK"
                    fcb       C$CR,C$LF,0
Diag14              fcc       "SG14 AUTOEX CHILD"
                    fcb       C$CR,C$LF,0
Diag15              fcc       "SG15 AUTOEX DONE"
                    fcb       C$CR,C$LF,0
Diag16              fcc       "SG16 SHELL CHAIN"
                    fcb       C$CR,C$LF,0
DiagFatal           fcc       "SGE FATAL"
                    fcb       C$CR,C$LF,0
                    endc

* Identity routine
* Exit: A = $02 (Wildbits/Jr), $12 (Wildbits/K), $1A (Wildbits/Jr2), $16 (Wildbits/K2)

ShowMachType        ldx       #SYS0
                    lda       7,x
                    cmpa      #$02                          Jr?
                    beq       @showJr
                    cmpa      #$16
                    beq       @showK2
                    cmpa      #$1A
                    beq       @showJr2
                    cmpa      #$12
                    bne       bye@
@showK              ldb       #'K
                    lbra      PUTC
@showK2             bsr       @showK
                    bra       @show2
@showJr             lbsr      PRINTS
                    fcc       " Jr"
                    fcb       0
bye@                rts
@showJr2            bsr       @showJr
@show2              ldb       #'2
                    lbra      PUTC
                                                                                
**********************************************************
* SysGo Entry Point
**********************************************************
__start
                    ifne      BOOT_DIAG
                    lbsr      BootDiagInit
                    endc
                    BOOTLOG   Diag00
                    leax      >IcptRtn,pcr
                    os9       F$Icpt
                    BOOTLOG   Diag01

* Set default time
                    leax      DefTime,pcr
                    os9       F$STime             set current time to start ticker (RTC will update time at top of minute)
                    BOOTLOG   Diag02

* Change DATA & EXEC directories
                    leax      Init,pcr
                    clra
                    pshs      u
                    os9       F$Link
                    tfr       u,x
                    puls      u
                    lbcs      DeadEnd
                    stx       InitAddr,u
                    BOOTLOG   Diag03
                    ldd       SysStr,x
                    leax      d,x
                    lda       #READ.
                    os9       I$ChgDir
                    lbcs      DeadEnd
                    BOOTLOG   Diag04
                    leax      >ExecDir,pcr
                    lda       #EXEC.
                    os9       I$ChgDir            change the execution directory
                    BOOTLOG   Diag05

* Fork scfg -dl here
* sets sys/defaultsettings if exists
* Show banner
DoScrnInit
                    BOOTLOG   Diag06
                    pshs      u
                    leax      >InitScrn,pcr
                    leau      >InitScrn2,pcr
                    ldd       #256
                    ldy       #InitScrnL2
                    os9       F$Fork
                    bcs       Next@               startup failed
                    BOOTLOG   Diag07
                    os9       F$Wait
                    BOOTLOG   Diag08
Next@               puls      u

* Write OS name and Machine name strings
DoInit              ldx       InitAddr,u
                    ldd       OSName,x            point to OS name in INIT module
                    leax      d,x                 
                    lbsr      PUTS
                    lbsr      PUTCR
                    ldx       InitAddr,u
                    ldd       InstallName,x       point to install name in INIT module
                    leax      d,x
                    lbsr      PUTS
                    lbsr      ShowMachType
                    lbsr      PRINTS
                    fcc       / - /
                    fcb       $0
                    ldx       InitAddr,u
                    ldd       BuildInfo,x       point to build info in INIT module
                    leax      d,x
                    lbsr      PUTS
                    lbsr      PUTCR
                    BOOTLOG   Diag09
                    pshs      u,y

* Fork shell startup here
DoStartup
                    BOOTLOG   Diag10
                    ifne      RUN_STARTUP
                    leax      >Shell,pcr
                    leau      >Startup,pcr
                    ldd       #256
                    ldy       #StartupL
                    os9       F$Fork
                    bcs       DoAuto              startup failed
                    BOOTLOG   Diag11
                    os9       F$Wait
                    BOOTLOG   Diag12
                    endc

* Fork AutoEx here
DoAuto
                    BOOTLOG   Diag13
                    leax      >AutoEx,pcr
                    leau      >CRtn,pcr
                    ldd       #256
                    ldy       #$0001
                    os9       F$Fork
                    bcs       next@               autoex failed
                    BOOTLOG   Diag14
                    os9       F$Wait
                    BOOTLOG   Diag15
next@               puls      u,y
FrkShell            leax      >ShellPrm,pcr
                    leay      ,u
                    ldb       #ShellPL
loop@               lda       ,x+
                    sta       ,y+
                    decb
                    bne       loop@
* Fork final shell here
                    BOOTLOG   Diag16
                    leax      >Shell,pcr
                    lda       #$01                D = 256 (B already 0 from above)
                    ldy       #ShellPL
                    ifgt      Level-1
                    os9       F$Chain             this should not return
                    ldb       #$06                it did! Fatal. Load error code
                    bra       Crash
DeadEnd             ldb       #$04                error code
Crash
                    BOOTLOG   DiagFatal
                    jmp       <D.Crash            fatal error
                    else
                    os9       F$Fork              perform the fork
                    bcs       DeadEnd             branch if error
                    os9       F$Wait              else wait
                    bcc       FrkShell            and refork if no error
DeadEnd
                    BOOTLOG   DiagFatal
DeadLoop            bra       DeadLoop            else loop forever
                    endc

IcptRtn             rti

                    ifne      BOOT_DIAG
* Initialize the system UART without installing an IRQ handler or SCF device.
* This logger is excluded from DriveWire builds because DriveWire owns the UART.
BootDiagInit        pshs      cc,d,x
                    ldx       #UART.Base
                    clr       1,x                 disable all UART interrupts
                    lda       3,x                 read the line-control register
                    ora       #$80                expose the divisor latches
                    sta       3,x
                    ldd       #BootDiagDivisor
                    sta       1,x                 divisor high byte
                    stb       ,x                  divisor low byte
                    lda       #$03                8 data bits, no parity, one stop bit
                    sta       3,x                 hide divisor latches and set framing
                    lda       #$07                enable and clear both FIFOs
                    sta       2,x
                    lda       #$03                assert DTR and RTS
                    sta       4,x
                    puls      cc,d,x,pc

* Write the zero-terminated string at X. The bounded transmitter wait makes the
* diagnostics observational: absent or faulty UART hardware cannot block boot.
BootDiagWrite       pshs      cc,d,x,y,u
                    ldu       #UART.Base
next@               lda       ,x+
                    beq       done@
                    ldy       #$FFFF
wait@               ldb       5,u                 read the line-status register
                    bitb      #$20                transmitter holding register empty?
                    bne       send@
                    leay      -1,y
                    bne       wait@
                    bra       done@               abandon the message on timeout
send@               sta       ,u                  transmit the character
                    bra       next@
done@               puls      cc,d,x,y,u,pc
                    endc

                    endsect

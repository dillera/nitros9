                    ifndef    DWBaud
DWBaud              equ       115200
                    endc

UARTClock           equ       25175000
UARTDivisor         equ       (UARTClock+(DWBaud*8))/(DWBaud*16)

DWInit
* Initialize the baud rate.
                    ldx       #UART.Base
                    lda       UART_LCR,x
                    ora       #LCR_DLB
                    sta       UART_LCR,x

                    pshs      b                   preserve caller's B register
                    ldd       #UARTDivisor         use the nearest integer divisor for the selected baud rate
                    sta       UART_DLH,x           write the high divisor byte while DLAB is set
                    stb       UART_DLL,x           write the low divisor byte while DLAB is set
                    puls      b                   restore caller's B register

                    lda       UART_LCR,x
                    eora      #LCR_DLB
                    sta       UART_LCR,x
* Initialize serial parameters.
                    lda       #LCR_PARITY_NONE|LCR_STOPBIT_1|LCR_DATABITS_8
                    anda      #0x7F
                    sta       UART_LCR,x

                    lda       #%11000000          FIFO mode is always on and it has only 14 Bytes
                    sta       UART_FCR,x
* Read until no more data left.
loop2@              lda       UART_TRHB,x         read byte from TX/RX holding register
                    lda       UART_LSR,x          get the LSR register value
                    bita      #LSR_DATA_AVAIL     test for data available
                    bne       loop2@              if available, get byte
                    rts

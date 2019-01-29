"""
This file is part of nucypher.

nucypher is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

nucypher is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with nucypher.  If not, see <https://www.gnu.org/licenses/>.
"""

import json
import os

import click

from nucypher.blockchain.eth.actors import Deployer
from nucypher.blockchain.eth.chains import Blockchain
from nucypher.blockchain.eth.interfaces import BlockchainInterface
from nucypher.cli.config import nucypher_deployer_config
from nucypher.cli.types import EIP55_CHECKSUM_ADDRESS, EXISTING_READABLE_FILE
from nucypher.config.constants import DEFAULT_CONFIG_ROOT


@click.command()
@click.argument('action')
@click.option('--force', is_flag=True)
@click.option('--poa', help="Inject POA middleware", is_flag=True)
@click.option('--no-compile', help="Disables solidity contract compilation", is_flag=True)
@click.option('--provider-uri', help="Blockchain provider's URI", type=click.STRING)
@click.option('--contract-name', help="Deploy a single contract by name", type=click.STRING)
@click.option('--deployer-address', help="Deployer's checksum address", type=EIP55_CHECKSUM_ADDRESS)
@click.option('--allocation-infile', help="Input path for token allocation JSON file", type=EXISTING_READABLE_FILE)
@nucypher_deployer_config
def deploy(click_config,
           action,
           poa,
           provider_uri,
           deployer_address,
           contract_name,
           allocation_infile,
           no_compile,
           force):
    """Manage contract and registry deployment"""

    def __connect(deployer_address=None):

        # Ensure config root exists
        if not os.path.exists(DEFAULT_CONFIG_ROOT):
            os.makedirs(DEFAULT_CONFIG_ROOT)

        # Connect to Blockchain
        blockchain = Blockchain.connect(provider_uri=provider_uri, deployer=True, compile=not no_compile, poa=poa)

        if not deployer_address:
            etherbase = blockchain.interface.w3.eth.accounts[0]
            deployer_address = etherbase
        click.confirm("Deployer Address is {} - Continue?".format(deployer_address), abort=True)

        deployer = Deployer(blockchain=blockchain, deployer_address=deployer_address)

        return deployer

    # The Big Three
    if action == "contracts":
        deployer = __connect(deployer_address)
        secrets = click_config.collect_deployment_secrets()

        # Track tx hashes, and new agents
        __deployment_transactions = dict()
        __deployment_agents = dict()

        if force:
            deployer.blockchain.interface.registry._destroy()

        try:
            txhashes, agents = deployer.deploy_network_contracts(miner_secret=bytes(secrets.miner_secret, encoding='utf-8'),
                                                                 policy_secret=bytes(secrets.policy_secret, encoding='utf-8'))
        except BlockchainInterface.InterfaceError:
            raise  # TODO: Handle registry management here (it may already exists)
        else:
            __deployment_transactions.update(txhashes)

        # User Escrow Proxy
        deployer.deploy_escrow_proxy(secret=bytes(secrets.escrow_proxy_secret, encoding='utf-8'))
        click.secho("Deployed!", fg='green', bold=True)

        #
        # Deploy Single Contract
        #
        if contract_name:

            try:
                deployer_func = deployer.deployers[contract_name]
            except KeyError:
                message = "No such contract {}. Available contracts are {}".format(contract_name, deployer.deployers.keys())
                click.secho(message, fg='red', bold=True)
                raise click.Abort()
            else:
                _txs, _agent = deployer_func()

        registry_outfile = deployer.blockchain.interface.registry.filepath
        click.secho('\nDeployment Transaction Hashes for {}'.format(registry_outfile), bold=True, fg='blue')
        for contract_name, transactions in __deployment_transactions.items():

            heading = '\n{} ({})'.format(contract_name, agents[contract_name].contract_address)
            click.secho(heading, bold=True)
            click.echo('*'*(42+3+len(contract_name)))

            total_gas_used = 0
            for tx_name, txhash in transactions.items():
                receipt = deployer.blockchain.wait_for_receipt(txhash=txhash)
                total_gas_used += int(receipt['gasUsed'])

                if receipt['status'] == 1:
                    click.secho("OK", fg='green', nl=False, bold=True)
                else:
                    click.secho("Failed", fg='red', nl=False, bold=True)
                click.secho(" | {}".format(tx_name), fg='yellow', nl=False)
                click.secho(" | {}".format(txhash.hex()), fg='yellow', nl=False)
                click.secho(" ({} gas)".format(receipt['cumulativeGasUsed']))

                click.secho("Block #{} | {}\n".format(receipt['blockNumber'], receipt['blockHash'].hex()))

        click.secho("Cumulative Gas Consumption: {} gas\n".format(total_gas_used), bold=True, fg='blue')

        if not force and click.confirm("Save transaction hashes to JSON file?"):
            file = click.prompt("Enter output filepath", type=click.File(mode='w'))  # TODO: Save Txhashes
            file.write(json.dumps(__deployment_transactions))
            click.secho("Wrote transaction hashes file to {}".format(file.path), fg='green')

    elif action == "allocations":
        deployer = __connect(deployer_address=deployer_address)
        if not allocation_infile:
            allocation_infile = click.prompt("Enter allocation data filepath")
        deployer.deploy_beneficiaries_from_file(allocation_data_filepath=allocation_infile)

    else:
        raise click.BadArgumentUsage

import sys
import copy
import argparse
import progressbar

import numpy as np
import matplotlib.pyplot as plt

import torch

from GraphDecompositionBO.graphGP.sampler.sample_hyper import slice_hyper
from GraphDecompositionBO.graphGP.sampler.sample_edgeweight import slice_edgeweight
from GraphDecompositionBO.graphGP.sampler.sample_partition import gibbs_partition
from GraphDecompositionBO.graphGP.sampler.tool_partition import compute_unit_in_group, group_input, ind_to_perturb

from GraphDecompositionBO.graphGP.kernels.diffusionkernel import DiffusionKernel
from GraphDecompositionBO.graphGP.models.gp_regression import GPRegression
from GraphDecompositionBO.graphGP.inference.inference import Inference

from GraphDecompositionBO.experiments.GP_ablation.data_loader import load_highorderbinary, load_maxsat


def COMBO_GP_sample(model, input_data, output_data, categories, list_of_adjacency, log_beta, sorted_partition, n_sample, n_burn=0, n_thin=1):
	'''
	:param model:
	:param input_data:
	:param output_data:
	:param categories:  1d np.array
	:param list_of_adjacency:
	:param log_beta:
	:param sorted_partition:
	:param n_sample:
	:param n_burn:
	:param n_thin:

	:return:
	'''
	sample_hyper = []
	sample_log_beta = []
	sample_partition = []
	sample_freq = []
	sample_basis = []

	log_beta_sample = log_beta
	fourier_freq_list = model.kernel.fourier_freq_list
	fourier_basis_list = model.kernel.fourier_basis_list

	bar = progressbar.ProgressBar(max_value=n_sample * n_thin + n_burn)
	for s in range(n_sample * n_thin + n_burn):
		slice_hyper(model=model, input_data=input_data, output_data=output_data, categories=categories, sorted_partition=sorted_partition)

		shuffled_beta_ind = range(len(list_of_adjacency))
		np.random.shuffle(shuffled_beta_ind)
		for beta_ind in shuffled_beta_ind:
			# In each sampler, model.kernel fourier_freq_list, fourier_basis_list are updated.
			slice_tuple = slice_edgeweight(model=model, input_data=input_data, output_data=output_data,
			                               categories=categories, list_of_adjacency=list_of_adjacency,
			                               log_beta=log_beta_sample, sorted_partition=sorted_partition,
			                               fourier_freq_list=fourier_freq_list, fourier_basis_list=fourier_basis_list,
			                               ind=beta_ind)
			log_beta_sample, fourier_freq_list, fourier_basis_list = slice_tuple
		if s >= n_burn and (s + 1) % n_thin == 0:
			sample_hyper.append(model.param_to_vec())
			sample_log_beta.append(log_beta_sample.clone())
			sample_partition.append(copy.deepcopy(sorted_partition))
			sample_freq.append([elm.clone() for elm in fourier_freq_list])
			sample_basis.append([elm.clone() for elm in fourier_basis_list])
		bar.update(value=s + 1)
		sys.stdout.flush()
	return sample_hyper, sample_log_beta, sample_partition, sample_freq, sample_basis


def GOLD_GP_sample(model, input_data, output_data, categories, list_of_adjacency, log_beta, sorted_partition, n_sample, n_burn=0, n_thin=1):
	'''
	:param model:
	:param input_data:
	:param output_data:
	:param categories:  1d np.array
	:param list_of_adjacency:
	:param log_beta:
	:param sorted_partition:
	:param n_sample:
	:param n_burn:
	:param n_thin:

	:return:
	'''
	sample_hyper = []
	sample_log_beta = []
	sample_partition = []
	sample_freq = []
	sample_basis = []

	partition_sample = sorted_partition
	log_beta_sample = log_beta
	fourier_freq_list = model.kernel.fourier_freq_list
	fourier_basis_list = model.kernel.fourier_basis_list

	shuffled_partition_ind = []
	bar = progressbar.ProgressBar(max_value=n_sample * n_thin + n_burn)
	for s in range(0, n_sample * n_thin + n_burn):
		# In 'Batched High-dimensional Bayesian Optimization via Structural Kernel Learning', similar additive structure is updated for every 50 iterations(evaluations)
		# This may be due to too much variability if decomposition is learned every iterations.
		# Thus, when multiple points are sampled, sweeping all inds for each sample may be not a good practice
		# For example if there are 50 variables and 10 samples are needed, then after shuffling indices, and 50/10 thinning can be used.
		if len(shuffled_partition_ind) == 0:
			shuffled_partition_ind = range(len(list_of_adjacency))
			np.random.shuffle(shuffled_partition_ind)
		partition_ind = ind_to_perturb(sorted_partition=partition_sample, categories=categories)
		# partition_ind = shuffled_partition_ind.pop()
		gibbs_tuple = gibbs_partition(model=model, input_data=input_data, output_data=output_data,
		                              categories=categories, list_of_adjacency=list_of_adjacency,
		                              log_beta=log_beta_sample, sorted_partition=partition_sample,
		                              fourier_freq_list=fourier_freq_list, fourier_basis_list=fourier_basis_list,
		                              ind=partition_ind)
		partition_sample, fourier_freq_list, fourier_basis_list = gibbs_tuple
		slice_hyper(model=model, input_data=input_data, output_data=output_data, categories=categories, sorted_partition=partition_sample)

		shuffled_beta_ind = range(len(list_of_adjacency))
		np.random.shuffle(shuffled_beta_ind)
		for beta_ind in shuffled_beta_ind:
			# In each sampler, model.kernel fourier_freq_list, fourier_basis_list are updated.
			slice_tuple = slice_edgeweight(model=model, input_data=input_data, output_data=output_data,
			                               categories=categories, list_of_adjacency=list_of_adjacency,
			                               log_beta=log_beta_sample, sorted_partition=partition_sample,
			                               fourier_freq_list=fourier_freq_list, fourier_basis_list=fourier_basis_list,
			                               ind=beta_ind)
			log_beta_sample, fourier_freq_list, fourier_basis_list = slice_tuple
		if s >= n_burn and (s + 1) % n_thin == 0:
			sample_hyper.append(model.param_to_vec())
			sample_log_beta.append(log_beta_sample.clone())
			sample_partition.append(copy.deepcopy(partition_sample))
			sample_freq.append([elm.clone() for elm in fourier_freq_list])
			sample_basis.append([elm.clone() for elm in fourier_basis_list])
		bar.update(value=s + 1)
		sys.stdout.flush()
	return sample_hyper, sample_log_beta, sample_partition, sample_freq, sample_basis


def evaluate_sample(model, train_input, train_output, test_input, test_output, categories, sample_hyper,
                    sample_log_beta, sample_partition, sample_freq, sample_basis):
	assert len(sample_hyper) == len(sample_freq) == len(sample_basis)
	n_sample = len(sample_hyper)
	train_mll_sum = 0
	for i in range(n_sample):
		model.kernel.fourier_freq_list = [elm.clone() for elm in sample_freq[i]]
		model.kernel.fourier_basis_list = [elm.clone() for elm in sample_basis[i]]
		unit_in_group = compute_unit_in_group(sorted_partition=sample_partition[i], categories=categories)
		train_grouped_input = group_input(input_data=train_input, sorted_partition=sample_partition[i],
		                                  unit_in_group=unit_in_group)
		inference = Inference((train_grouped_input, train_output), model)
		train_mll_sum += -inference.negative_log_likelihood(hyper=sample_hyper[i])
	train_mll_avg = train_mll_sum / float(n_sample)

	test_pll_sum = 0
	test_pmean_sum = 0
	test_pvar_sum = 0
	for i in range(n_sample):
		model.kernel.fourier_freq_list = [elm.clone() for elm in sample_freq[i]]
		model.kernel.fourier_basis_list = [elm.clone() for elm in sample_basis[i]]
		unit_in_group = compute_unit_in_group(sorted_partition=sample_partition[i], categories=categories)
		train_grouped_input = group_input(input_data=train_input, sorted_partition=sample_partition[i], unit_in_group=unit_in_group)
		test_grouped_input = group_input(input_data=test_input, sorted_partition=sample_partition[i], unit_in_group=unit_in_group)
		inference = Inference((train_grouped_input, train_output), model)
		test_sample_pred_mean, test_sample_pred_var = inference.predict(test_grouped_input, hyper=sample_hyper[i])

		test_pll_sum += gaussian_log_likelihood(test_output, test_sample_pred_mean, test_sample_pred_var)
		test_pmean_sum += test_sample_pred_mean
		test_pvar_sum += test_sample_pred_var
	test_pll_avg = test_pll_sum / float(n_sample)
	test_pmean_avg = test_pmean_sum / float(n_sample)
	test_pvar_avg = test_pvar_sum / float(n_sample)
	print('')
	# test_result = torch.cat([test_output, test_pmean_avg, test_pvar_avg, test_pvar_avg ** 0.5], dim=1)
	# print('TEST')
	# for i in range(test_result.size(0)):
	# 	print(' '.join([('%+6.4E' % test_result[i, j].item()) for j in range(4)]))
	test_pll = torch.mean(test_pll_avg)
	plt.errorbar(test_output.numpy(), test_pmean_avg.numpy(), fmt='b+', yerr=test_pvar_avg.numpy() ** 0.5, color='b', ecolor='g', alpha=0.5)
	plt.scatter(train_output.numpy(), train_output.numpy(), s=60, facecolor='none', edgecolors='r')
	plt.title(('(%d/%d) train MLL:%+2f, test PLL avg:%+.2f ' % (train_output.numel(), test_output.numel(), train_mll_avg.item(), test_pll.item())) + ('Learned' if sample_partition[0] != sample_partition[-1] else 'Fixed'))
	plt.ylim([torch.min(train_output).item(), torch.max(train_output).item()])
	plt.show()
	return train_mll_avg, test_pll


def gaussian_log_likelihood(data, mean, var):
	return -0.5 * np.log(2) - 0.5 * torch.log(np.pi * var) - 0.5 * (data - mean) ** 2 / var


def GP_regression_sampling(data_type, train_data_scale, n_sample, n_thin, n_burn, random_seed, learn_decomposition):
	dataset = load_highorderbinary(data_type=data_type, train_data_scale=train_data_scale, random_seed=random_seed)
	# dataset = load_maxsat(data_type=data_type, train_data_scale=train_data_scale, random_seed=random_seed)
	(train_input, train_output), (test_input, test_output) = dataset
	n_variables = train_input.size(1)
	categories = np.array([2 for _ in range(n_variables)])
	list_of_adjacency = []
	init_log_beta = torch.zeros(n_variables)
	init_sorted_partition = [[m] for m in range(n_variables)]
	# init_sorted_partition = [[0, 1], [2], [3], [4]]
	categories = np.array([np.prod(categories[subset]) for subset in init_sorted_partition])

	fourier_freq_list = []
	fourier_basis_list = []
	for i in range(n_variables):
		adjmat = torch.diag(torch.ones(1), -1) + torch.diag(torch.ones(1), 1)
		list_of_adjacency.append(adjmat)
		laplacian = torch.diag(torch.sum(adjmat, dim=0)) - adjmat
		eigval, eigvec = torch.symeig(laplacian, eigenvectors=True)
		fourier_freq_list.append(eigval)
		fourier_basis_list.append(eigvec)
	kernel = DiffusionKernel(fourier_freq_list=fourier_freq_list, fourier_basis_list=fourier_basis_list)

	model = GPRegression(kernel=kernel)
	model.init_param(train_output)

	sampler = GOLD_GP_sample if learn_decomposition else COMBO_GP_sample
	posterior_sample = sampler(model=model, input_data=train_input, output_data=train_output,
	                           categories=categories, list_of_adjacency=list_of_adjacency,
	                           log_beta=init_log_beta, sorted_partition=init_sorted_partition,
	                           n_sample=n_sample, n_burn=n_burn, n_thin=n_thin)
	sample_hyper, sample_log_beta, sample_partition, sample_freq, sample_basis = posterior_sample
	mll, pll = evaluate_sample(model, train_input, train_output, test_input, test_output, categories, sample_hyper, sample_log_beta, sample_partition, sample_freq, sample_basis)
	print(' %+10.4f |    %+10.4f |' % (mll, pll))
	return mll, pll


def run_sampling(data_type, train_data_scale, n_sample, n_thin, n_burn, random_seed, learn_decomposition):
	exp_info_str = '%d data type - %d train data scale' % (data_type, train_data_scale)
	exp_info_str += '\n%d samples / %d thin / %d burn-in %s' % (n_sample, n_thin, n_burn, '/ Learn Decomposition' if learn_decomposition else '')
	print(exp_info_str)
	n_repeat = 1
	mll_list_ = []
	pll_list_ = []
	for _ in range(n_repeat):
		mll_, pll_ = GP_regression_sampling(data_type, train_data_scale, n_sample, n_thin, n_burn, random_seed, learn_decomposition)
		mll_list_.append(mll_)
		pll_list_.append(pll_)
	result_str_ = '\n'.join([('          %+12.4f | %+12.4f' % (mll_, pll_)) for mll_, pll_ in zip(mll_list_, pll_list_)])
	result_str_ += '\nMean :    %+12.4f | %+12.4f' % (np.mean(mll_list_), np.mean(pll_list_))
	result_str_ += '\nStd.Err : %12.4f | %12.4f' % (np.std(mll_list_) / n_repeat ** 0.5, np.std(pll_list_) / n_repeat ** 0.5)
	result_str_ += '\nMedian  : %+12.4f | %+12.4f' % (np.median(mll_list_), np.median(pll_list_))
	print(exp_info_str)
	print(result_str_)


if __name__ == '__main__':
	parser_ = argparse.ArgumentParser(description='GOLD : Gaussian Process Regression')
	parser_.add_argument('--data_type', dest='data_type', type=int)
	parser_.add_argument('--train_data_scale', dest='train_data_scale', type=int)
	parser_.add_argument('--n_sample', dest='n_sample', type=int, default=10)
	parser_.add_argument('--n_thin', dest='n_thin', type=int, default=2)
	parser_.add_argument('--n_burn', dest='n_burn', type=int, default=10)
	parser_.add_argument('--random_seed', dest='random_seed', type=int, default=1)
	parser_.add_argument('--learn_decomposition', dest='learn_decomposition', action='store_true', default=False)
	args_ = parser_.parse_args()

	run_sampling(data_type=1, train_data_scale=1, n_sample=10, n_thin=2, n_burn=20, random_seed=1, learn_decomposition=False)
	run_sampling(data_type=1, train_data_scale=1, n_sample=10, n_thin=2, n_burn=20, random_seed=1, learn_decomposition=True)
	run_sampling(data_type=1, train_data_scale=3, n_sample=10, n_thin=2, n_burn=20, random_seed=1, learn_decomposition=False)
	run_sampling(data_type=1, train_data_scale=3, n_sample=10, n_thin=2, n_burn=20, random_seed=1, learn_decomposition=True)

	run_sampling(data_type=2, train_data_scale=1, n_sample=10, n_thin=2, n_burn=20, random_seed=1, learn_decomposition=False)
	run_sampling(data_type=2, train_data_scale=1, n_sample=10, n_thin=2, n_burn=20, random_seed=1, learn_decomposition=True)
	run_sampling(data_type=2, train_data_scale=3, n_sample=10, n_thin=2, n_burn=20, random_seed=1, learn_decomposition=False)
	run_sampling(data_type=2, train_data_scale=3, n_sample=10, n_thin=2, n_burn=20, random_seed=1, learn_decomposition=True)

	# run_sampling(data_type=3, train_data_scale=1, n_sample=50, n_thin=1, n_burn=10, random_seed=1, learn_decomposition=False)
	# run_sampling(data_type=3, train_data_scale=1, n_sample=50, n_thin=1, n_burn=10, random_seed=1, learn_decomposition=True)
	# run_sampling(data_type=3, train_data_scale=3, n_sample=50, n_thin=2, n_burn=10, random_seed=1, learn_decomposition=False)
	# run_sampling(data_type=3, train_data_scale=3, n_sample=50, n_thin=2, n_burn=10, random_seed=1, learn_decomposition=True)

